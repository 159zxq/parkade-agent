"""Mock 停车场服务 —— stub 版（对接依据：docs/接口契约.md）。

A0 阶段目标：所有路由**先返回契约结构的假数据**，让 Agent 开发不被阻塞。
B 的任务卡 B1–B8：把每个 `TODO(Bx)` 换成真实逻辑（车位增减、票据表、冲突校验、
相机图渲染），契约字段名/结构不许改（要改走变更流程）。

启动：uvicorn mock_service.main:app --reload --port 8000
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from PIL import Image
from pydantic import BaseModel

from common.pricing import calc_fee, load_rates
from mock_service.clock import clock
from mock_service.state import CAMERA_TABLE, INITIAL_OCCUPIED

FMT = "%Y-%m-%dT%H:%M:%S"


def _placeholder_jpg() -> bytes:
    """B5 之前的占位图（灰色 64×64 JPEG）；B5 用 PIL 渲染真实车牌图替换。"""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (178, 178, 178)).save(buf, "JPEG", quality=60)
    return buf.getvalue()


_PLACEHOLDER_JPG = _placeholder_jpg()

areas_cfg = load_rates()["areas"]

app = FastAPI(title="停车场 Mock 服务（stub）", version="0.1-stub")


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


@app.get("/lots/status")
def lots_status(area: str | None = None):
    # TODO(B1): 从可变状态计算 free/occupied，随 /entry /exit 增减
    result = []
    for code, meta in areas_cfg.items():
        if area and code != area.strip().upper():
            continue
        total = int(meta["total"])
        occupied = len(INITIAL_OCCUPIED[code])
        result.append({"area": code, "name": meta["name"], "total": total,
                       "free": total - occupied, "occupied": occupied})
    if not result:
        return _err(400, "INVALID_PARAM", f"未知区域: {area}")
    return {"ok": True, "updated_at": clock.now().strftime(FMT), "areas": result}


@app.post("/entry")
def entry(req: EntryReq):
    # TODO(B2): ALREADY_INSIDE / NO_FREE_SPOT 校验；占用车位；票据号递增
    plate = req.plate.strip().upper()
    return {"ok": True, "ticket_id": "T-0001", "plate": plate,
            "area": req.area or "A", "entry_time": clock.now().strftime(FMT)}


@app.post("/exit")
def exit_(req: ExitReq):
    # TODO(B3): 按票据表真实入场时间计费；NOT_INSIDE 校验；dry_run 不改状态
    plate = req.plate.strip().upper()
    now = clock.now()
    fee = calc_fee("小型车", now - timedelta(hours=2), now)
    return {"ok": True, "plate": plate, "vehicle_type": "小型车", "area": "A",
            "ticket_id": "T-0001",
            "entry_time": fee["entry_time"], "exit_time": fee["exit_time"],
            "duration_min": fee["duration_min"], "free_minutes": fee["free_minutes"],
            "billable_minutes": fee["billable_minutes"], "units": fee["units"],
            "subtotal": fee["subtotal"], "cap_applied": fee["cap_applied"],
            "total": fee["total"]}


@app.post("/reserve")
def reserve(req: ReserveReq):
    # TODO(B6): RESERVE_CONFLICT 校验；预约表；keep_until 计算
    plate = req.plate.strip().upper()
    try:
        rt = datetime.fromisoformat(req.reserve_time)
    except ValueError:
        return _err(400, "INVALID_PARAM", "reserve_time 需为 YYYY-MM-DDTHH:MM:SS")
    keep = rt + timedelta(minutes=30)
    return {"ok": True, "reserve_id": "R-0001", "plate": plate, "area": req.area or "A",
            "reserve_time": rt.strftime(FMT), "keep_until": keep.strftime(FMT),
            "status": "active"}


@app.get("/camera/{camera_id}/capture")
def capture(camera_id: str, variant: str = "normal"):
    # TODO(B5): PIL 渲染 640×480 车牌图；variant ∈ normal|blur|occluded
    if camera_id not in CAMERA_TABLE:
        return _err(404, "CAMERA_NOT_FOUND", f"未知相机: {camera_id}")
    return Response(content=_PLACEHOLDER_JPG, media_type="image/jpeg")


@app.get("/debug/truth/{camera_id}")
def truth(camera_id: str):
    cam = CAMERA_TABLE.get(camera_id)
    if cam is None:
        return _err(404, "CAMERA_NOT_FOUND", f"未知相机: {camera_id}")
    return {"ok": True, "camera_id": camera_id, "plate": cam["plate"],
            "vehicle_type": cam["vehicle_type"], "variant_active": "normal"}


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
    return get_time()


@app.post("/debug/time/reset")
def reset_time():
    clock.reset()
    return get_time()


@app.post("/debug/reset")
def reset_all():
    # TODO(B): 连同车位/票据/预约一起恢复初始（stub 版仅重置时钟）
    clock.reset()
    return {"ok": True}
