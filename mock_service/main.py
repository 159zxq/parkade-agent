"""停车场 Mock 服务主入口（FastAPI 路由）。

严格实现《接口契约》v1 §3 全部接口：
  GET  /camera/{camera_id}/capture
  GET  /debug/truth/{camera_id}
  GET  /lots/status
  POST /entry
  POST /exit
  POST /reserve
  GET  /debug/time
  POST /debug/time
  POST /debug/time/reset
  POST /debug/reset

启动：uvicorn mock_service.main:app --reload --port 8000
"""

from __future__ import annotations

import datetime as dt

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from common.pricing import calc_fee
from mock_service.camera import render_plate_image
from mock_service.clock import clock
from mock_service.state import (
    CAMERAS,
    DEFAULT_VEHICLE_TYPE,
    RESERVE_KEEP_MINUTES,
    fmt,
    normalize_plate,
    state,
)

app = FastAPI(title="Parkade Mock Service", version="1.0.0")

VALID_VARIANTS = {"normal", "blur", "occluded"}


# ---------------------------------------------------------------------------
# 统一错误响应：HTTP 400/404 + {"ok": false, "error": {"code","message"}}
# ---------------------------------------------------------------------------
def err(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": {"code": code, "message": message}})


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 3.1 相机抓拍
# ---------------------------------------------------------------------------
@app.get("/camera/{camera_id}/capture")
def capture(camera_id: str, variant: str = Query("normal")):
    if camera_id not in CAMERAS:
        return err("CAMERA_NOT_FOUND", f"未知相机: {camera_id}", status=404)
    if variant not in VALID_VARIANTS:
        return err("INVALID_PARAM", f"variant 只能为 {sorted(VALID_VARIANTS)}")
    jpeg = render_plate_image(camera_id, variant)
    return Response(content=jpeg, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# 3.2 真值对账（仅测试用，Agent 严禁调用）
# ---------------------------------------------------------------------------
@app.get("/debug/truth/{camera_id}")
def truth(camera_id: str, variant: str = Query("normal")):
    if camera_id not in CAMERAS:
        return err("CAMERA_NOT_FOUND", f"未知相机: {camera_id}", status=404)
    cam = CAMERAS[camera_id]
    return {
        "ok": True,
        "camera_id": camera_id,
        "plate": cam["plate"],
        "vehicle_type": cam["vehicle_type"],
        "variant_active": variant,
    }


# ---------------------------------------------------------------------------
# 3.3 车位状态
# ---------------------------------------------------------------------------
@app.get("/lots/status")
def lots_status(area: str | None = Query(None)):
    counts = state.area_counts()
    if area:
        area = area.upper()
        counts = [c for c in counts if c["area"] == area]
    return {"ok": True, "updated_at": fmt(clock.now()), "areas": counts}


# ---------------------------------------------------------------------------
# 3.4 入场
# ---------------------------------------------------------------------------
@app.post("/entry")
def entry(req: EntryReq):
    plate = normalize_plate(req.plate)

    # 契约 §3.4：入场规则只有两条——重复入场、无空位。
    # 预约与入场互不约束（预约仅表意向，不绑定票据、不占位）。
    if plate in state.parked:
        return err("ALREADY_INSIDE", f"车牌 {plate} 已在场内")

    # 选区域
    if req.area:
        area = req.area.upper()
        if area not in {"A", "B", "C"}:
            return err("INVALID_PARAM", f"未知区域: {req.area}")
        if state.free_in(area) <= 0:
            return err("NO_FREE_SPOT", f"{area} 区已满")
    else:
        area = state.first_area_with_free()
        if area is None:
            return err("NO_FREE_SPOT", "全区无空位")

    # 车型：优先用相机映射，其次默认小型车
    vehicle_type = DEFAULT_VEHICLE_TYPE
    if req.camera_id:
        if req.camera_id not in CAMERAS:
            return err("CAMERA_NOT_FOUND", f"未知相机: {req.camera_id}", status=404)
        vehicle_type = CAMERAS[req.camera_id]["vehicle_type"]

    ticket = state.add_entry(plate, area, vehicle_type)
    return {
        "ok": True,
        "ticket_id": ticket.ticket_id,
        "plate": plate,
        "area": area,
        "entry_time": fmt(ticket.entry_time),
    }


# ---------------------------------------------------------------------------
# 3.5 出场结算
# ---------------------------------------------------------------------------
@app.post("/exit")
def exit_(req: ExitReq):
    plate = normalize_plate(req.plate)
    ticket = state.tickets.get(plate)
    if ticket is None:
        return err("NOT_INSIDE", f"车牌 {plate} 不在场内")

    exit_time = clock.now()
    fee = calc_fee(ticket.vehicle_type, ticket.entry_time, exit_time)

    body = {
        "ok": True,
        "plate": plate,
        "vehicle_type": ticket.vehicle_type,
        "area": ticket.area,
        "ticket_id": ticket.ticket_id,
        "entry_time": fmt(ticket.entry_time),
        "exit_time": fmt(exit_time),
        **fee,
    }

    if not req.dry_run:
        state.remove_entry(plate)

    return body


# ---------------------------------------------------------------------------
# 3.6 预约
# ---------------------------------------------------------------------------
@app.post("/reserve")
def reserve(req: ReserveReq):
    plate = normalize_plate(req.plate)

    if state.active_reservation(plate) is not None:
        return err("RESERVE_CONFLICT", f"车牌 {plate} 已有未过期预约")

    try:
        reserve_time = dt.datetime.fromisoformat(req.reserve_time)
    except ValueError:
        return err("INVALID_PARAM", f"reserve_time 格式错误: {req.reserve_time}")

    area = req.area.upper() if req.area else None
    if area is not None and area not in {"A", "B", "C"}:
        return err("INVALID_PARAM", f"未知区域: {req.area}")

    res = state.add_reservation(plate, area, reserve_time)
    return {
        "ok": True,
        "reserve_id": res.reserve_id,
        "plate": plate,
        "area": res.area,
        "reserve_time": fmt(res.reserve_time),
        "keep_until": fmt(res.keep_until),
        "status": res.status,
    }


# ---------------------------------------------------------------------------
# 3.7 时钟与状态调试
# ---------------------------------------------------------------------------
def _time_payload() -> dict:
    return {
        "ok": True,
        "virtual_time": fmt(clock.now()),
        "real_time": fmt(clock.now() - dt.timedelta(minutes=clock.offset_minutes)),
        "offset_minutes": clock.offset_minutes,
    }


@app.get("/debug/time")
def get_time():
    return _time_payload()


@app.post("/debug/time")
async def set_time(request: Request):
    body = await request.json()
    if "set" in body and body["set"]:
        try:
            clock.set_time(body["set"])
        except ValueError:
            return err("INVALID_PARAM", f"set 格式错误: {body['set']}")
    elif "offset_minutes" in body:
        try:
            clock.add_offset(int(body["offset_minutes"]))
        except (TypeError, ValueError):
            return err("INVALID_PARAM", "offset_minutes 必须为整数")
    else:
        return err("INVALID_PARAM", "需提供 set 或 offset_minutes")
    return _time_payload()


@app.post("/debug/time/reset")
def reset_time():
    clock.reset()
    return _time_payload()


@app.post("/debug/reset")
def reset_all():
    state.reset()
    return {"ok": True, "updated_at": fmt(clock.now())}


@app.get("/")
def root():
    return {"ok": True, "service": "parkade-mock", "docs": "/docs"}
