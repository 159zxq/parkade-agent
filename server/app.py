"""停车场智能体 Web 服务（Vue 前端的后端，端口 8100）。

职责：
- 用户管理：SQLite 落盘（data/web.sqlite），支持添加/删除/切换用户；
- 聊天：每用户独立 thread_id（Agent 记忆落盘），历史消息入库供界面恢复；
- 识别：上传图片（模拟硬件抓拍上传）与 mock 相机抓拍，均走同一双通道+仲裁链路；
- 看板：读取/调试 mock 的车位与虚拟时间（透传 /debug/*）；
- 静态托管：web/dist 构建产物（生产模式）；开发模式用 vite dev + /api 代理。

启动（项目根目录）：
    uvicorn server.app:app --port 8100
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contextlib import contextmanager

import httpx  # noqa: E402
from fastapi import FastAPI, File, Form, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.graph import build_agent, chat_with_context  # noqa: E402
from agent.vision import PLATE_RE, recognize_plate, recognize_upload  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

DB_PATH = ROOT / "data" / "web.sqlite"
DIST_DIR = ROOT / "web" / "dist"
CAPTURE_DIR = ROOT / "data" / "captures"
UPLOAD_DIR = ROOT / "data" / "uploads"

app = FastAPI(title="停车场智能体 Web 服务", version="1.0")

# 单 Agent 实例 + 全局锁：多用户会话共享 Agent（各自 thread_id），串行执行保证状态安全
_agent = None
_agent_lock = threading.Lock()


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


# ---------------------------------------------------------------------------
# 数据库（用户 + 消息历史）——每操作独立连接、用完即关（避免句柄泄漏）
# ---------------------------------------------------------------------------
@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            plate TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)""")
        # 旧库迁移：users 无 plate 列时补列（老用户车牌为空 → 不注入车辆上下文）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "plate" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN plate TEXT NOT NULL DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            snippets TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL)""")
        n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if n == 0:
            conn.execute("INSERT INTO users (name, plate, created_at) VALUES (?, ?, ?)",
                         ("演示用户", "京A12345", _now()))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


init_db()


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------
@app.get("/api/users")
def list_users():
    with _db() as conn:
        rows = conn.execute("SELECT id, name, plate, created_at FROM users ORDER BY id").fetchall()
    return {"ok": True, "users": [dict(r) for r in rows]}


class UserReq(BaseModel):
    name: str
    plate: str


def normalize_plate(plate: str) -> str:
    return "".join(str(plate).split()).upper()


@app.post("/api/users")
def add_user(req: UserReq):
    name = req.name.strip()
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "用户名不能为空"})
    plate = normalize_plate(req.plate)
    if not plate:
        return JSONResponse(status_code=400, content={"ok": False, "error": "必须填写车牌号（用于判断车辆是否在场）"})
    if PLATE_RE.fullmatch(plate) is None:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": "车牌格式不正确，示例：京A12345"})
    try:
        with _db() as conn:
            cur = conn.execute("INSERT INTO users (name, plate, created_at) VALUES (?, ?, ?)",
                               (name, plate, _now()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={"ok": False, "error": "用户名已存在"})
    return {"ok": True, "user": {"id": uid, "name": name, "plate": plate, "created_at": _now()}}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    with _db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "用户不存在"})
        conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"ok": True}


@app.get("/api/users/{user_id}/messages")
def user_messages(user_id: int):
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, text, snippets, created_at FROM messages "
            "WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    return {"ok": True, "messages": [
        {"role": r["role"], "text": r["text"],
         "snippets": json.loads(r["snippets"] or "[]"),
         "created_at": r["created_at"]} for r in rows]}


@app.delete("/api/users/{user_id}/messages")
def clear_messages(user_id: int):
    """清空界面历史（Agent 的记忆/thread 不受影响）。"""
    with _db() as conn:
        conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 聊天
# ---------------------------------------------------------------------------
class ChatReq(BaseModel):
    user_id: int
    message: str


def _append(conn, user_id: int, role: str, text: str, snippets: list) -> None:
    conn.execute("INSERT INTO messages (user_id, role, text, snippets, created_at) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (user_id, role, text, json.dumps(snippets, ensure_ascii=False), _now()))


def _vehicle_context(user_row) -> str | None:
    """查 mock 单车状态 → 构造"当前用户车辆"上下文块（注入临时 system prompt）。

    面向车主：包含本人车辆在场状态/所在区域（仅供本人查询自己车辆）、月卡、
    预约情况、上次停车（离开时间/费用/缴费状态）——来自 mock 落盘记录库，重启不丢。
    """
    plate = (user_row["plate"] or "").strip()
    if not plate:
        return None
    try:
        r = httpx.get(f"{MOCK_BASE}/vehicle/{plate}", timeout=8)
        v = r.json()
    except httpx.HTTPError:
        return None
    if not v.get("ok"):
        return None
    lines = [
        "【当前用户车辆】（每轮实时查询的系统数据，优先级最高。",
        "这是**车主本人**的车辆档案数据，仅用于回答车主询问自己车辆的情况；",
        "与历史对话中任何关于该车辆状态的陈述——包括你自己此前轮次的回答——冲突时，",
        "一律以本块为准，并主动更正此前的说法，不要沿用旧答案；",
        "回答该用户与本人车辆相关的问题时不要反问用户的车牌。此数据不写入对话历史。）",
        f"车主：{user_row['name']}｜车牌：{v['plate']}",
    ]
    if v["inside"]:
        lines.append(f"状态：**在场**（停在 {v['area']} 区）")
        lines.append(f"入场时间：{v['entry_time']}｜已停放：{v['duration_min']} 分钟")
        if v.get("ticket_id"):
            lines.append(f"票据号：{v['ticket_id']}")
    else:
        lines.append("状态：**不在场**（当前未入场）")
    monthly = v.get("monthly")
    if monthly:
        lines.append(f"月卡：**生效中**（有效至 {monthly['valid_until']}，出场免计费）")
    else:
        lines.append("月卡：无生效中的月卡（如需办理可咨询月卡价格与渠道）")
    rsv = v.get("reservation")
    if rsv:
        lines.append(f"预约情况：已预约 {rsv.get('area') or '系统分配'} 区"
                     f"，保留至 {rsv['keep_until']}（未过期，入场可核销）")
    last = v.get("last_visit")
    if last:
        paid_txt = "已缴费" if last.get("paid") else "未缴费"
        fee = last.get("fee")
        fee_txt = f"{fee:.2f} 元" if isinstance(fee, (int, float)) else "—"
        lines.append(f"上次停车：{last['exit_time']} 离场｜"
                     f"时长 {last['duration_min']} 分钟｜费用 {fee_txt}｜{paid_txt}")
    return "\n".join(lines)


@app.post("/api/chat")
def chat(req: ChatReq):
    msg = req.message.strip()
    if not msg:
        return JSONResponse(status_code=400, content={"ok": False, "error": "消息不能为空"})
    with _db() as conn:
        user_row = conn.execute("SELECT id, name, plate FROM users WHERE id=?",
                                (req.user_id,)).fetchone()
    if user_row is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "用户不存在"})
    thread_id = f"web-user-{req.user_id}"   # 每用户独立记忆
    vehicle_ctx = _vehicle_context(user_row)
    try:
        with _agent_lock:                    # 串行执行，避免共享 Agent 并发问题
            reply, snippets = chat_with_context(get_agent(), thread_id, msg, vehicle_ctx)
    except Exception as e:  # noqa: BLE001
        detail = str(e)
        if "429" in detail or "frequent" in detail.lower():
            reply, snippets = "（请求过于频繁，请稍等几秒后重发）", []
        else:
            reply, snippets = f"（服务暂时不可用，请稍后重试）{detail[:160]}", []

    with _db() as conn:
        _append(conn, req.user_id, "user", msg, [])
        _append(conn, req.user_id, "assistant", reply, snippets)
    return {"ok": True, "reply": reply, "snippets": snippets,
            "vehicle": {"plate": user_row["plate"], "context_injected": bool(vehicle_ctx)},
            "created_at": _now()}


# ---------------------------------------------------------------------------
# 识别（上传图片 / 相机抓拍）——均走双通道+仲裁，识别成功即触发闸机动作
# ---------------------------------------------------------------------------
CAMERA_AREA = {"cam-1": "A", "cam-2": "B", "cam-3": "C"}   # 入口相机物理所在区


def _gate_action(plate: str, *, camera_id: str | None = None,
                 action: str = "entry", area: str | None = None) -> dict:
    """闸机动作（识别成功后的放行语义，模拟硬件闸机联动）：

    - 出口（`action="exit"` 或 cam-4）→ 真实结算出场（返回费用明细）；
    - 入口 → 登记入场。区域分配优先级：显式 `area`（网页可选 A/B/C）
      > 入口相机所在区（cam-1=A/cam-2=B/cam-3=C） > 自动分配（A→B→C 找空位）。
    返回 {action, ok, message, ...}；mock 业务错误/不可达时 ok=false（不失真：如实转述）。
    """
    do_exit = action == "exit" or camera_id == "cam-4"
    try:
        if do_exit:
            r = httpx.post(f"{MOCK_BASE}/exit", json={"plate": plate}, timeout=15)
            data = r.json()
            if r.status_code == 200 and data.get("ok"):
                return {"action": "exit", "ok": True,
                        "message": f"出口结算完成：{data['duration_min']} 分钟，"
                                   f"应收 {data['total']:.2f} 元（已缴费，可放行）",
                        "ticket_id": data["ticket_id"], "duration_min": data["duration_min"],
                        "total": data["total"], "paid": data.get("paid", True)}
            code = (data.get("error") or {}).get("code")
            msg = (data.get("error") or {}).get("message") or "出场失败"
            if code == "NOT_INSIDE":
                msg = f"车牌 {plate} 不在场内，无法结算出场"
            return {"action": "exit", "ok": False, "message": msg}

        payload: dict = {"plate": plate}
        if camera_id:
            payload["camera_id"] = camera_id
        target_area = area or (CAMERA_AREA.get(camera_id) if camera_id else None)
        if target_area:
            payload["area"] = target_area
        r = httpx.post(f"{MOCK_BASE}/entry", json=payload, timeout=15)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            return {"action": "entry", "ok": True,
                    "message": f"已入场：{data['area']} 区 · 票号 {data['ticket_id']}"
                               f"（入场时间 {data['entry_time']}）",
                    "ticket_id": data["ticket_id"], "area": data["area"],
                    "entry_time": data["entry_time"],
                    "reservation_consumed": data.get("reservation_consumed", False)}
        code = (data.get("error") or {}).get("code")
        msg = (data.get("error") or {}).get("message") or "入场失败"
        if code == "ALREADY_INSIDE":
            msg = f"车牌 {plate} 已在场内，未重复登记"
        return {"action": "entry", "ok": False, "message": msg}
    except httpx.HTTPError as e:
        return {"action": "exit" if do_exit else "entry", "ok": False,
                "message": f"停车场服务不可达: {e}"}


@app.post("/api/recognize")
async def recognize(file: UploadFile = File(...), action: str = Form("entry"),
                    area: str = Form("")):
    """上传识别（表单可带 action=entry|exit 与 area=A|B|C|空=自动）。

    识别成功即执行闸机动作：entry → 登记入场（area 指定区域，空则自动分配）；
    exit → 结算出场（与出口相机一致）。
    """
    action = (action or "entry").strip().lower()
    if action not in ("entry", "exit"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "action 只能为 entry 或 exit"})
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"ok": False, "error": "空文件"})
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"ok": False, "error": "图片超过 10MB"})
    if not (data[:4] == b"\x89PNG" or data[:2] == b"\xff\xd8"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "仅支持 PNG/JPEG 图片"})
    with _agent_lock:  # 与聊天共用锁：识别链路含在线 VLM 调用，避免与其并发挤占
        result = recognize_upload(data, file.filename or "upload.jpg")
    result["image_url"] = _media_url("uploads", Path(result["image_path"]).name)
    # 闸机联动：识别成功（仲裁出车牌）即按所选动作执行（入场可选区域 / 出场结算）
    area_sel = (area or "").strip().upper() or None
    if area_sel and area_sel not in ("A", "B", "C"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "area 只能为 A/B/C 或留空"})
    gate = (_gate_action(result["plate"], action=action, area=area_sel)
            if result.get("plate") else None)
    return {"ok": True, "result": result, "gate": gate}


class CaptureReq(BaseModel):
    camera_id: str


@app.post("/api/capture")
def capture(req: CaptureReq):
    with _agent_lock:
        result = recognize_plate(req.camera_id, "normal")
    if result.get("image_path"):
        result["image_url"] = _media_url("captures", Path(result["image_path"]).name)
    # 闸机联动：cam-1/2/3 = 各区入口（进所在区）；cam-4 = 出口（结算出场）
    gate = _gate_action(result["plate"], camera_id=req.camera_id) if result.get("plate") else None
    return {"ok": True, "result": result, "gate": gate}


def _media_url(kind: str, filename: str) -> str:
    return f"/api/media/{kind}/{filename}"


@app.get("/api/media/{kind}/{filename}")
def media(kind: str, filename: str):
    """受控图片读取：仅允许 data/captures 与 data/uploads 下的文件。"""
    base = {"captures": CAPTURE_DIR, "uploads": UPLOAD_DIR}.get(kind)
    if base is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "未知目录"})
    target = (base / filename).resolve()
    if not str(target).startswith(str(base.resolve())) or not target.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": "文件不存在"})
    return FileResponse(str(target))


# ---------------------------------------------------------------------------
# 看板（透传 mock 状态与调试）
# ---------------------------------------------------------------------------
def _mock(method: str, path: str, payload: dict | None = None):
    try:
        if method == "GET":
            r = httpx.get(f"{MOCK_BASE}{path}", timeout=10)
        else:
            r = httpx.post(f"{MOCK_BASE}{path}", json=payload or {}, timeout=10)
        return JSONResponse(status_code=r.status_code, content=r.json())
    except httpx.HTTPError as e:
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": {"code": "MOCK_UNREACHABLE",
                                                            "message": f"Mock 服务不可达: {e}"}})


@app.get("/api/lots")
def lots():
    return _mock("GET", "/lots/status")


@app.get("/api/records")
def records(plate: str | None = None, limit: int = 20):
    """进出场 / 缴费 / 预约 留痕查询（透传 mock 落盘记录库）。"""
    qs = f"?limit={limit}" + (f"&plate={plate}" if plate else "")
    return _mock("GET", f"/records{qs}")


class PayReq(BaseModel):
    plate: str
    type: str = "spot"               # spot=现缴（当场结清出场费）/ monthly=月卡
    vehicle_type: str | None = None  # 仅月卡用


@app.post("/api/pay")
def pay(req: PayReq):
    """缴费（现缴 spot / 月卡 monthly；透传 mock /pay）。"""
    payload = {"plate": req.plate.strip().upper(), "type": req.type}
    if req.vehicle_type:
        payload["vehicle_type"] = req.vehicle_type
    return _mock("POST", "/pay", payload)


class FeeReq(BaseModel):
    plate: str


@app.post("/api/fee")
def fee(req: FeeReq):
    """当前出场费试算（dry_run，不改状态；未在场返回业务错误）。"""
    return _mock("POST", "/exit", {"plate": req.plate.strip().upper(), "dry_run": True})


class LotsDebugReq(BaseModel):
    area: str
    total: int | None = None
    occupied: int | None = None


@app.post("/api/debug/lots")
def debug_lots(req: LotsDebugReq):
    return _mock("POST", "/debug/lots", req.model_dump(exclude_none=True))


class TimeReq(BaseModel):
    offset_minutes: int | None = None
    set: str | None = None


@app.get("/api/debug/time")
def get_time():
    return _mock("GET", "/debug/time")


@app.post("/api/debug/time")
def set_time(req: TimeReq):
    return _mock("POST", "/debug/time", req.model_dump(exclude_none=True))


@app.post("/api/debug/time/reset")
def reset_time():
    return _mock("POST", "/debug/time/reset")


@app.post("/api/debug/reset")
def reset_all():
    return _mock("POST", "/debug/reset")


@app.get("/api/health")
def health():
    mock_ok = True
    try:
        mock_ok = httpx.get(f"{MOCK_BASE}/lots/status", timeout=3).status_code == 200
    except httpx.HTTPError:
        mock_ok = False
    return {"ok": True, "mock": mock_ok, "time": _now()}


# ---------------------------------------------------------------------------
# 静态托管（生产：web/dist；开发：vite dev server + /api 代理）
# ---------------------------------------------------------------------------
if DIST_DIR.exists() and (DIST_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="web")
