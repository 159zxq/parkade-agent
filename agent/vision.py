"""车牌识别 v0（A1 最小实现：仅本地 OCR 通道）。

A2（W2）在此基础上扩展为：本地 OCR ∥ 视觉 API 双通道 + 仲裁矩阵 + 图片哈希缓存。
本文件保持可独立调用，A2 只改内部实现，不动返回结构（契约 `{plate, source, ocr, vlm, confidence}`）。
"""
from __future__ import annotations

import io

import httpx

from common.config import MOCK_BASE

_LPR = None  # HyperLPR3 模型单例（首次调用加载，约 2-5 秒）


def _lpr():
    global _LPR
    if _LPR is None:
        from hyperlpr3 import LicensePlateCatcher

        _LPR = LicensePlateCatcher()
    return _LPR


def recognize_plate_v0(camera_id: str, variant: str = "normal") -> dict:
    """抓拍单帧并本地 OCR 识别。返回契约结构（vlm 字段 A2 填充）。"""
    empty = {"plate": None, "source": "failed", "ocr": None, "vlm": None,
             "confidence": None, "cached": False, "camera_id": camera_id}
    try:
        resp = httpx.get(f"{MOCK_BASE}/camera/{camera_id}/capture",
                         params={"variant": variant}, timeout=15)
    except httpx.HTTPError as e:
        return {**empty, "error": f"相机服务不可达: {e}"}
    if resp.status_code == 404:
        return {**empty, "error": f"未知相机: {camera_id}"}
    if resp.status_code != 200:
        return {**empty, "error": f"抓拍失败: HTTP {resp.status_code}"}

    # TODO(A2)：在此并行接入视觉 API 通道 + 仲裁矩阵；v0 仅本地 OCR
    plate, confidence = None, None
    try:
        import numpy as np
        from PIL import Image

        arr = np.array(Image.open(io.BytesIO(resp.content)).convert("RGB"))
        results = _lpr()(arr)  # [(text, confidence, type, box), ...]
        if results:
            plate = results[0][0].replace(" ", "")
            confidence = float(results[0][1])
    except Exception as e:  # noqa: BLE001
        return {**empty, "error": f"本地 OCR 异常: {e}"}

    if plate:
        return {"plate": plate, "source": "ocr_only",
                "ocr": {"plate": plate, "confidence": round(confidence, 4)},
                "vlm": None, "confidence": round(confidence, 4),
                "cached": False, "camera_id": camera_id}
    return {**empty, "error": "识别失败，请调整位置后重拍"}
