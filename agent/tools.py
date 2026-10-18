"""Agent 工具层 —— 五个工具的统一接口。

错误处理策略：工具内部不抛异常，一律返回 {"ok": false, "error": ...}，
让 Agent 把失败原样转述给用户（统一错误码）。
"""
from __future__ import annotations

import httpx
from langchain_core.tools import tool

from agent.vision import recognize_plate as _vision_recognize
from common.config import MOCK_BASE

_TIMEOUT = 15.0


def _get(path: str, **params):
    try:
        r = httpx.get(f"{MOCK_BASE}{path}", params=params or None, timeout=_TIMEOUT)
        return r.json()
    except httpx.HTTPError as e:
        return {"ok": False, "error": {"code": "MOCK_UNREACHABLE", "message": f"停车场服务不可达: {e}"}}


def _post(path: str, payload: dict):
    try:
        r = httpx.post(f"{MOCK_BASE}{path}", json=payload, timeout=_TIMEOUT)
        return r.json()
    except httpx.HTTPError as e:
        return {"ok": False, "error": {"code": "MOCK_UNREACHABLE", "message": f"停车场服务不可达: {e}"}}


@tool
def recognize_plate(camera_id: str) -> dict:
    """对指定相机抓拍单帧并识别车牌（双通道：本地 OCR + 视觉模型，自动仲裁）。
    相机 id：cam-1/cam-2/cam-3 为各入口，cam-4 为出口。
    返回 source 字段：both_agree(双通道一致)/both_conflict(存在分歧,以OCR为准)/
    ocr_only/vlm_only/failed(识别失败,请引导用户重拍)。plate 为 null 即识别失败。"""
    return _vision_recognize(camera_id)


@tool
def query_parking_status(area: str = "") -> dict:
    """查询停车场车位状态。area 可选 'A'/'B'/'C' 指定区域，留空返回全部区域。"""
    return _get("/lots/status", **({"area": area.strip().upper()} if area.strip() else {}))


@tool
def register_entry(plate: str, camera_id: str = "") -> dict:
    """车辆入场登记。plate 为车牌号（如 京A12345）。成功返回 ticket_id 与入场时间。"""
    payload: dict = {"plate": plate}
    if camera_id:
        payload["camera_id"] = camera_id
    return _post("/entry", payload)


@tool
def calculate_exit_fee(plate: str) -> dict:
    """计算指定车牌当前的出场费用（只报价出场，不真正放行、不改变车位状态）。
    返回 duration_min/units/total 等明细。"""
    return _post("/exit", {"plate": plate, "dry_run": True})


@tool
def reserve_spot(plate: str, reserve_time: str, area: str = "") -> dict:
    """预约车位。reserve_time 格式 'YYYY-MM-DDTHH:MM:SS'；area 可选 'A'/'B'/'C'。
    备注：演示环境下如需"未来的时间"，可先由管理员注入虚拟时间。"""
    payload: dict = {"plate": plate, "reserve_time": reserve_time}
    if area:
        payload["area"] = area
    return _post("/reserve", payload)


ALL_TOOLS = [recognize_plate, query_parking_status, register_entry,
             calculate_exit_fee, reserve_spot]
