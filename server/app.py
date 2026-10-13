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
from fastapi import FastAPI, File, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.graph import build_agent, chat_with_trace  # noqa: E402
from agent.vision import recognize_plate, recognize_upload  # noqa: E402
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
            created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            snippets TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL)""")
        n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if n == 0:
            conn.execute("INSERT INTO users (name, created_at) VALUES (?, ?)",
                         ("演示用户", _now()))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


init_db()


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------
@app.get("/api/users")
def list_users():
    with _db() as conn:
        rows = conn.execute("SELECT id, name, created_at FROM users ORDER BY id").fetchall()
    return {"ok": True, "users": [dict(r) for r in rows]}


class UserReq(BaseModel):
    name: str


@app.post("/api/users")
def add_user(req: UserReq):
    name = req.name.strip()
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "用户名不能为空"})
    try:
        with _db() as conn:
            cur = conn.execute("INSERT INTO users (name, created_at) VALUES (?, ?)",
                               (name, _now()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={"ok": False, "error": "用户名已存在"})
    return {"ok": True, "user": {"id": uid, "name": name, "created_at": _now()}}


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


@app.post("/api/chat")
def chat(req: ChatReq):
    msg = req.message.strip()
    if not msg:
        return JSONResponse(status_code=400, content={"ok": False, "error": "消息不能为空"})
    thread_id = f"web-user-{req.user_id}"   # 每用户独立记忆
    try:
        with _agent_lock:                    # 串行执行，避免共享 Agent 并发问题
            reply, snippets = chat_with_trace(get_agent(), thread_id, msg)
    except Exception as e:  # noqa: BLE001
        detail = str(e)
        if "429" in detail or "frequent" in detail.lower():
            reply, snippets = "（请求过于频繁，请稍等几秒后重发）", []
        else:
            reply, snippets = f"（服务暂时不可用，请稍后重试）{detail[:160]}", []

    with _db() as conn:
        _append(conn, req.user_id, "user", msg, [])
        _append(conn, req.user_id, "assistant", reply, snippets)
    return {"ok": True, "reply": reply, "snippets": snippets, "created_at": _now()}


# ---------------------------------------------------------------------------
# 识别（上传图片 / 相机抓拍）——均走双通道+仲裁
# ---------------------------------------------------------------------------
@app.post("/api/recognize")
async def recognize(file: UploadFile = File(...)):
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
    return {"ok": True, "result": result}


class CaptureReq(BaseModel):
    camera_id: str


@app.post("/api/capture")
def capture(req: CaptureReq):
    with _agent_lock:
        result = recognize_plate(req.camera_id, "normal")
    if result.get("image_path"):
        result["image_url"] = _media_url("captures", Path(result["image_path"]).name)
    return {"ok": True, "result": result}


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
