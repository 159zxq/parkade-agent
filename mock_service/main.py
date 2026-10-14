"""Mock 停车场服务 —— 全接口实现（对接依据：docs/接口契约.md）。

骨架来自成员A 的 stub（A0），成员B 完成 B1–B6：把每个 `TODO(Bx)` 换成真实逻辑
（车位增减、票据表、冲突校验、相机图渲染）。契约字段名/结构不许改。

2026-10-14（契约 v1.3，需求方迭代）：
- 票据/预约落盘 SQLite（mock_service/db.py → data/mock.sqlite）：
  进入时间、离开时间（出场落）、缴费状态（离开默认已缴费）、预约情况全留痕；
- 进程重启自动恢复在场票据与未过期预约（根治"车已进场、重启后被说成没进场"）；
- 看板余位 = 总 − 占用 − **预约保留**（被预约占用的车位在看板上同步变化）；
- 预约车入场优先进入预约区域并自动核销预约。

启动：uvicorn mock_service.main:app --reload --port 8000
"""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from common.pricing import calc_fee, load_rates
from mock_service.camera import render_capture
from mock_service.clock import clock
from mock_service.db import db
from mock_service.state import (
    CAMERA_TABLE,
    DEFAULT_VEHICLE_TYPE,
    FMT,
    normalize_plate,
    state,
)

areas_cfg = load_rates()["areas"]
reserve_cfg = load_rates().get("reserve", {})
RESERVE_KEEP_MINUTES = int(reserve_cfg.get("keep_minutes", 30))

app = FastAPI(title="停车场 Mock 服务", version="1.0")

# 启动即建库并恢复上次运行的在场/预约状态（重启不丢，契约 v1.3）
db.init()
state.restore_from_db()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": {"code": code, "message": message}})


class EntryReq(BaseModel):
    plate: str
    camera_id: str | None = None
    area: str | None = None


class ExitReq(BaseModel):
    plate: str
    dry_run: bool = False


class ReserveReq(BaseModel):
    plate: str
    reserve_time: str
    area: str | None = None


class TimeReq(BaseModel):
    offset_minutes: int | None = None
    set: str | None = None


class PayReq(BaseModel):
    plate: str
    amount: float | None = None   # 不传：在场车按其当前出场费预缴；不在场则必传


# ---------------------------------------------------------------------------
# B1 — 车位状态（容量取自 state.capacity，可经 /debug/lots 调试）
# ---------------------------------------------------------------------------
@app.get("/lots/status")
def lots_status(area: str | None = None):
    state.purge_expired()   # 过期预约即时释放，看板余位实时
    result = []
    for code, meta in areas_cfg.items():
        if area and code != area.strip().upper():
            continue
        total = state.capacity_in(code)
        occupied = state.occupied_in(code)
        reserved = state.reserved_in(code)
        result.append({"area": code, "name": meta["name"], "total": total,
                       "free": max(0, total - occupied - reserved),
                       "occupied": occupied, "reserved": reserved})
    if not result:
        return _err(400, "INVALID_PARAM", f"未知区域: {area}")
    return {"ok": True, "updated_at": clock.now().strftime(FMT), "areas": result}


# ---------------------------------------------------------------------------
# B2 — 入场
# ---------------------------------------------------------------------------
@app.post("/entry")
def entry(req: EntryReq):
    plate = normalize_plate(req.plate)
    state.purge_expired()

    if plate in state.occupied:
        return _err(400, "ALREADY_INSIDE", f"车牌 {plate} 已在场内")

    if req.camera_id is not None and req.camera_id not in CAMERA_TABLE:
        return _err(404, "CAMERA_NOT_FOUND", f"未知相机: {req.camera_id}")

    res = state.active_reservation(plate)   # 有未过期预约的车：可用自己的保留位
    own_area = res.area if res is not None else None

    if req.area:
        area = req.area.strip().upper()
        if area not in areas_cfg:
            return _err(400, "INVALID_PARAM", f"未知区域: {req.area}")
        # 预约本区：看物理空位（保留位就是给它留的）；否则看非保留余位
        if area == own_area:
            if state.physical_free_in(area, state.capacity_in(area)) <= 0:
                return _err(400, "NO_FREE_SPOT", f"{area} 区已满")
        elif state.free_in(area, state.capacity_in(area)) <= 0:
            return _err(400, "NO_FREE_SPOT", f"{area} 区已满（含预约保留位）")
    else:
        # 预约车优先进预约区（保留位可用时）；否则按 A→B→C 找非保留余位
        if own_area and state.physical_free_in(own_area, state.capacity_in(own_area)) > 0:
            area = own_area
        else:
            area = next((c for c in areas_cfg
                         if state.free_in(c, state.capacity_in(c)) > 0), None)
            if area is None:
                return _err(400, "NO_FREE_SPOT", "全区无空位（含预约保留位）")

    vehicle_type = (CAMERA_TABLE[req.camera_id]["vehicle_type"]
                    if req.camera_id else DEFAULT_VEHICLE_TYPE)

    ticket = state.add_entry(plate, area, vehicle_type)
    reservation_consumed = state.consume_reservation(plate) is not None
    return {"ok": True, "ticket_id": ticket.ticket_id, "plate": plate,
            "area": area, "entry_time": ticket.entry_time.strftime(FMT),
            "reservation_consumed": reservation_consumed}


# ---------------------------------------------------------------------------
# B3 — 出场结算（费率必须走 common/pricing.py）
# ---------------------------------------------------------------------------
@app.post("/exit")
def exit_(req: ExitReq):
    plate = normalize_plate(req.plate)
    ticket = state.tickets.get(plate)
    if ticket is None:
        return _err(400, "NOT_INSIDE", f"车牌 {plate} 不在场内")

    exit_time = clock.now()
    fee = calc_fee(ticket.vehicle_type, ticket.entry_time, exit_time)
    prepaid = state.prepaid_total(plate)   # 出场前的预缴余额（出场即冲抵）

    body = {"ok": True, "plate": plate, "vehicle_type": ticket.vehicle_type,
            "area": ticket.area, "ticket_id": ticket.ticket_id,
            "entry_time": ticket.entry_time.strftime(FMT),
            "exit_time": exit_time.strftime(FMT),
            "duration_min": fee["duration_min"], "free_minutes": fee["free_minutes"],
            "billable_minutes": fee["billable_minutes"], "units": fee["units"],
            "subtotal": fee["subtotal"], "cap_applied": fee["cap_applied"],
            "total": fee["total"], "paid": True, "prepaid_used": prepaid}

    if not req.dry_run:
        # 落盘：离开时间 + 时长 + 费用 + 已缴费（离开默认已缴费，契约 v1.3）
        state.remove_entry(plate, exit_time=exit_time,
                           duration_min=fee["duration_min"], fee=fee["total"])
    return body


# ---------------------------------------------------------------------------
# B6 — 预约
# ---------------------------------------------------------------------------
@app.post("/reserve")
def reserve(req: ReserveReq):
    plate = normalize_plate(req.plate)
    state.purge_expired()

    if state.active_reservation(plate) is not None:
        return _err(400, "RESERVE_CONFLICT", f"车牌 {plate} 已有未过期预约")

    try:
        rt = datetime.fromisoformat(req.reserve_time)
    except ValueError:
        return _err(400, "INVALID_PARAM", "reserve_time 需为 YYYY-MM-DDTHH:MM:SS")

    if req.area:
        area = req.area.strip().upper()
        if area not in areas_cfg:
            return _err(400, "INVALID_PARAM", f"未知区域: {req.area}")
    else:
        # 未指定区域：默认保留到第一个有非保留余位的区（预约占用在看板上可见）
        area = next((c for c in areas_cfg
                     if state.free_in(c, state.capacity_in(c)) > 0), None)

    res = state.add_reservation(plate, area, rt, RESERVE_KEEP_MINUTES)
    return {"ok": True, "reserve_id": res.reserve_id, "plate": plate, "area": res.area,
            "reserve_time": res.reserve_time.strftime(FMT),
            "keep_until": res.keep_until.strftime(FMT), "status": res.status}


# ---------------------------------------------------------------------------
# 缴费通道（契约 v1.4）：模拟车主给车牌缴费（预缴，出场自动冲抵）
# ---------------------------------------------------------------------------
@app.post("/pay")
def pay(req: PayReq):
    plate = normalize_plate(req.plate)
    if not plate:
        return _err(400, "INVALID_PARAM", "车牌不能为空")

    ticket = state.tickets.get(plate)
    if req.amount is None:
        if ticket is None:
            return _err(400, "INVALID_PARAM",
                        f"车牌 {plate} 不在场，需指定缴费金额 amount（预缴）")
        fee = calc_fee(ticket.vehicle_type, ticket.entry_time, clock.now())
        amount = float(fee["total"])
    else:
        amount = float(req.amount)
        if amount <= 0:
            return _err(400, "INVALID_PARAM", "缴费金额需大于 0")

    pay_ = state.add_payment(plate, amount)
    return {"ok": True, "payment_id": pay_.payment_id, "plate": plate,
            "amount": pay_.amount, "pay_time": pay_.pay_time.strftime(FMT),
            "inside": ticket is not None,
            "ticket_id": pay_.ticket_id, "status": pay_.status,
            "prepaid_total": state.prepaid_total(plate)}


# ---------------------------------------------------------------------------
# B5 — 相机抓拍
# ---------------------------------------------------------------------------
@app.get("/camera/{camera_id}/capture")
def capture(camera_id: str, variant: str = "normal"):
    if camera_id not in CAMERA_TABLE:
        return _err(404, "CAMERA_NOT_FOUND", f"未知相机: {camera_id}")
    if variant not in ("normal", "blur", "occluded"):
        return _err(400, "INVALID_PARAM", f"variant 只能为 normal|blur|occluded，收到 {variant!r}")
    jpeg = render_capture(camera_id, variant)
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/debug/truth/{camera_id}")
def truth(camera_id: str, variant: str = "normal"):
    cam = CAMERA_TABLE.get(camera_id)
    if cam is None:
        return _err(404, "CAMERA_NOT_FOUND", f"未知相机: {camera_id}")
    return {"ok": True, "camera_id": camera_id, "plate": cam["plate"],
            "vehicle_type": cam["vehicle_type"], "variant_active": variant}


# ---------------------------------------------------------------------------
# 单车查询（契约 v1.2；v1.3 增预约情况与上次停车）
# ---------------------------------------------------------------------------
def _reservation_view(res) -> dict:
    return {"reserve_id": res.reserve_id, "area": res.area,
            "reserve_time": res.reserve_time.strftime(FMT),
            "keep_until": res.keep_until.strftime(FMT), "status": res.status}


@app.get("/vehicle/{plate}")
def vehicle_status(plate: str):
    p = normalize_plate(plate)
    state.purge_expired()
    area = state.occupied.get(p)
    ticket = state.tickets.get(p)
    res = state.reservations.get(p)

    last = db.last_visit(p)
    last_visit = None
    if last is not None:
        last_visit = {"exit_time": last["exit_time"], "duration_min": last["duration_min"],
                      "fee": last["fee"], "paid": last["paid"]}

    reservation = _reservation_view(res) if res is not None else None
    prepaid_total = state.prepaid_total(p)

    if area is None:
        return {"ok": True, "plate": p, "inside": False, "area": None,
                "vehicle_type": None, "ticket_id": None, "entry_time": None,
                "duration_min": None, "queried_at": clock.now().strftime(FMT),
                "reservation": reservation, "last_visit": last_visit,
                "prepaid_total": prepaid_total}
    duration = None
    if ticket is not None:
        duration = int((clock.now() - ticket.entry_time).total_seconds() // 60)
    return {"ok": True, "plate": p, "inside": True, "area": area,
            "vehicle_type": ticket.vehicle_type if ticket else DEFAULT_VEHICLE_TYPE,
            "ticket_id": ticket.ticket_id if ticket else None,
            "entry_time": ticket.entry_time.strftime(FMT) if ticket else None,
            "duration_min": duration, "queried_at": clock.now().strftime(FMT),
            "reservation": reservation, "last_visit": last_visit,
            "prepaid_total": prepaid_total}


# ---------------------------------------------------------------------------
# 记录查询（契约 v1.3）：进出场 / 缴费 / 预约 全留痕
# ---------------------------------------------------------------------------
@app.get("/records")
def records(plate: str | None = None, limit: int = 20):
    state.purge_expired()
    limit = max(1, min(int(limit), 100))
    p = normalize_plate(plate) if plate else None
    return {"ok": True, "plate": p,
            "tickets": db.list_tickets(p, limit),
            "reservations": db.list_reservations(p, limit),
            "payments": db.list_payments(p, limit)}


# ---------------------------------------------------------------------------
# B4 — 时钟与状态调试
# ---------------------------------------------------------------------------
@app.get("/debug/time")
def get_time():
    return {"ok": True, "virtual_time": clock.now().strftime(FMT),
            "real_time": clock.real_now().strftime(FMT),
            "offset_minutes": int(round(clock.offset_minutes()))}


@app.post("/debug/time")
def set_time(req: TimeReq):
    if req.offset_minutes is not None:
        clock.add_offset(req.offset_minutes)
    elif req.set:
        try:
            clock.set_time(req.set)
        except ValueError:
            return _err(400, "INVALID_PARAM", "set 需为 YYYY-MM-DDTHH:MM:SS")
    else:
        return _err(400, "INVALID_PARAM", "需要 offset_minutes 或 set")
    state.purge_expired()   # 快进后过期预约即时释放
    return get_time()


@app.post("/debug/time/reset")
def reset_time():
    clock.reset()
    return get_time()


@app.post("/debug/reset")
def reset_all():
    clock.reset()
    state.reset()
    db.reset()   # 记录库一并清空，回到演示初始（契约 v1.3）
    return {"ok": True, "updated_at": clock.now().strftime(FMT)}


# ---------------------------------------------------------------------------
# 看板调试（前端"车位看板调试"用，契约 v1.1 新增）
# ---------------------------------------------------------------------------
class LotsDebugReq(BaseModel):
    area: str
    total: int | None = None       # 改区域总车位数（>= 当前占用）
    occupied: int | None = None    # 直接设占用数（0..容量）


@app.post("/debug/lots")
def debug_lots(req: LotsDebugReq):
    area = req.area.strip().upper()
    if area not in areas_cfg:
        return _err(400, "INVALID_PARAM", f"未知区域: {req.area}")

    if req.total is not None:
        if req.total < state.occupied_in(area):
            return _err(400, "INVALID_PARAM",
                        f"总车位数不能小于当前占用数 {state.occupied_in(area)}")
        if req.total < 0:
            return _err(400, "INVALID_PARAM", "总车位数不能为负")
        state.set_capacity(area, req.total)

    if req.occupied is not None:
        if req.occupied < 0 or req.occupied > state.capacity_in(area):
            return _err(400, "INVALID_PARAM",
                        f"占用数需在 0..{state.capacity_in(area)} 之间（当前容量）")
        state.set_occupancy(area, req.occupied)

    state.purge_expired()
    meta = areas_cfg[area]
    return {"ok": True, "area": area, "name": meta["name"],
            "total": state.capacity_in(area), "occupied": state.occupied_in(area),
            "reserved": state.reserved_in(area),
            "free": max(0, state.capacity_in(area) - state.occupied_in(area)
                        - state.reserved_in(area)),
            "updated_at": clock.now().strftime(FMT)}
