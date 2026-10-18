"""双通道车牌识别与仲裁。

通道一：HyperLPR3 本地 OCR；
通道二：视觉大模型（common/config.py 的 VISION 独立配置组）。

仲裁矩阵（五分支，对应 tests/test_vision.py 的 TC-识别-01..05）：
  OCR成功 + VLM成功且一致 → both_agree      用结果，标记双通道一致
  OCR成功 + VLM成功不一致 → both_conflict   以 OCR 为准，标记存在分歧
  OCR成功 + VLM失败/超时  → ocr_only        OCR 结果
  OCR失败 + VLM成功       → vlm_only        VLM 兜底
  双失败                  → failed          提示重拍

实现要点：
- 双通道并行：ThreadPoolExecutor 两线程（本模块在同步工具调度下被调用，
  线程池避免嵌套事件循环问题）；
- 无缓存：每次调用都真实执行双通道识别（每次演示/测试均为真实链路）；
- 返回结构：{plate, source, ocr, vlm, confidence, camera_id, variant,
  origin, image_path, elapsed_ms}（识别失败时另附 error 说明）。
"""
from __future__ import annotations

import base64
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from common.config import MOCK_BASE, VISION

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = ROOT / "data" / "captures"

OCR_TIMEOUT = 8.0     # 本地 OCR 单通道超时（含首次模型加载）
VLM_TIMEOUT = 25.0    # 视觉 API 单通道超时（网关波动留余量）
VLM_RETRIES = 2       # 网关限流(429)/瞬时错误的重试次数（退避 1.5s/3s）

# 车牌：省份简称 + 字母 + 5~6 位字母数字
PLATE_RE = re.compile(
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,6}"
)

_LPR = None  # HyperLPR3 模型单例（首次调用加载，约 2-5 秒）


def _lpr():
    global _LPR
    if _LPR is None:
        from hyperlpr3 import LicensePlateCatcher

        _LPR = LicensePlateCatcher()
    return _LPR


# ---------------------------------------------------------------------------
# 通道一：本地 OCR
# ---------------------------------------------------------------------------
def ocr_channel(image_bytes: bytes) -> dict | None:
    """HyperLPR3 本地识别。成功返回 {"plate", "confidence"}；失败返回 None。"""
    try:
        import numpy as np
        from PIL import Image

        arr = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        results = _lpr()(arr)  # [(text, confidence, type, box), ...]
        if results:
            plate = str(results[0][0]).replace(" ", "").upper()
            return {"plate": plate, "confidence": round(float(results[0][1]), 4)}
    except Exception:  # noqa: BLE001  通道失败一律降级为 None（由仲裁处理）
        return None
    return None


# ---------------------------------------------------------------------------
# 通道二：视觉大模型
# ---------------------------------------------------------------------------
VLM_PROMPT = ("这是一张停车场抓拍图片。请识别图片中的车牌号码，只输出车牌号本身"
              "（省份简称+字母+数字，如 京A12345）。若车牌不清晰或无法确定，"
              "只输出四个字：无法识别")


def _vlm_raw_call(data_url: str) -> str:
    """按 VISION.api_mode 调用视觉模型（支持 Responses / Chat 双接口，auto 自动回退）。"""
    client = VISION.client(timeout=VLM_TIMEOUT)

    def via_chat() -> str:
        r = client.chat.completions.create(
            model=VISION.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": VLM_PROMPT},
            ]}])
        return r.choices[0].message.content or ""

    def via_responses() -> str:
        r = client.responses.create(
            model=VISION.model,
            input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": data_url},
                {"type": "input_text", "text": VLM_PROMPT},
            ]}])
        return r.output_text or ""

    mode = VISION.api_mode
    if mode == "responses":
        return via_responses()
    if mode == "chat":
        return via_chat()
    try:  # auto：先标准接口，网关要求 Responses 时自动切换
        return via_chat()
    except Exception as e:  # noqa: BLE001
        if "responses" in str(e).lower():
            return via_responses()
        raise


def vlm_channel(image_bytes: bytes) -> dict | None:
    """视觉模型识别。成功返回 {"plate", "raw"}；失败/超时/无法识别返回 None。

    含重试：网关限流(429)是实测过的真实情况（连续演示会触发），
    退避 1.5s/3s 重试两次，提升现场稳定性。
    """
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:image/jpeg;base64,{b64}"
    last_err: Exception | None = None
    for attempt in range(VLM_RETRIES + 1):
        try:
            text = _vlm_raw_call(data_url)
            if "无法识别" in text:
                return None
            m = PLATE_RE.search(text.replace(" ", ""))
            if m is None:
                return None
            return {"plate": m.group(0), "raw": text.strip()[:80]}
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < VLM_RETRIES:
                time.sleep(1.5 * (attempt + 1))  # 429 退避
    return None


# ---------------------------------------------------------------------------
# 仲裁（纯函数，离线可测）
# ---------------------------------------------------------------------------
def arbitrate(ocr: dict | None, vlm: dict | None) -> tuple[str, str | None, float | None]:
    """仲裁矩阵。返回 (source, plate, confidence)。"""
    if ocr and vlm:
        if ocr["plate"] == vlm["plate"]:
            return "both_agree", ocr["plate"], ocr.get("confidence")
        return "both_conflict", ocr["plate"], ocr.get("confidence")  # 分歧：以 OCR 为准
    if ocr:
        return "ocr_only", ocr["plate"], ocr.get("confidence")
    if vlm:
        return "vlm_only", vlm["plate"], None
    return "failed", None, None


# ---------------------------------------------------------------------------
# 主入口（同步；供 langchain 工具 / 测试脚本 / 前端调用）
# ---------------------------------------------------------------------------
def _capture(camera_id: str, variant: str) -> tuple[bytes | None, str | None]:
    try:
        resp = httpx.get(f"{MOCK_BASE}/camera/{camera_id}/capture",
                         params={"variant": variant}, timeout=15)
    except httpx.HTTPError as e:
        return None, f"相机服务不可达: {e}"
    if resp.status_code == 404:
        return None, f"未知相机: {camera_id}"
    if resp.status_code != 200:
        return None, f"抓拍失败: HTTP {resp.status_code}"
    return resp.content, None


def _wait(future, timeout: float) -> dict | None:
    try:
        return future.result(timeout=timeout)
    except Exception:  # noqa: BLE001  超时/异常 → 通道失败
        return None


def _save_capture(camera_id: str, variant: str, jpg: bytes) -> str:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / f"{camera_id}_{variant}.jpg"
    try:
        path.write_bytes(jpg)
    except OSError:
        pass
    return str(path)


UPLOAD_DIR = ROOT / "data" / "uploads"


def _save_upload(filename: str, data: bytes) -> str:
    import re as _re

    safe = _re.sub(r"[^\w.\-]", "_", filename or "upload")[-60:]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}"
    try:
        path.write_bytes(data)
    except OSError:
        pass
    return str(path)


def _data_url(image_bytes: bytes) -> str:
    """按魔数选择 MIME（PNG/JPEG），构造 data URL。"""
    mime = "image/png" if image_bytes[:4] == b"\x89PNG" else "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{mime};base64,{b64}"


def _dual_channel(jpg: bytes) -> tuple[dict | None, dict | None, int]:
    """双通道并行识别。返回 (ocr, vlm, 耗时ms)。"""
    t0 = time.perf_counter()
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_ocr = ex.submit(ocr_channel, jpg)
        f_vlm = ex.submit(vlm_channel, jpg)
        ocr = _wait(f_ocr, OCR_TIMEOUT)
        vlm = _wait(f_vlm, VLM_TIMEOUT)
    finally:
        ex.shutdown(wait=False)  # 超时的通道线程不阻塞返回
    return ocr, vlm, int((time.perf_counter() - t0) * 1000)


def recognize_image_bytes(jpg: bytes, *, image_path: str, origin: str,
                          camera_id: str | None = None,
                          variant: str | None = None) -> dict:
    """对任意图片字节执行双通道识别 + 仲裁（相机抓拍与网页上传共用同一链路）。"""
    ocr, vlm, ms = _dual_channel(jpg)
    source, plate, confidence = arbitrate(ocr, vlm)
    result = {"plate": plate, "source": source, "ocr": ocr, "vlm": vlm,
              "confidence": confidence, "camera_id": camera_id, "variant": variant,
              "origin": origin, "image_path": image_path, "elapsed_ms": ms}
    if source == "failed":
        result["error"] = ("识别失败，请调整位置后重拍" if origin == "camera"
                           else "识别失败，请更换更清晰的图片后重试")
    return result


def recognize_upload(image_bytes: bytes, filename: str = "upload.jpg") -> dict:
    """网页上传图片的识别入口（模拟硬件抓拍图上传）。走与相机完全相同的双通道+仲裁。"""
    image_path = _save_upload(filename, image_bytes)
    return recognize_image_bytes(image_bytes, image_path=image_path,
                                 origin="upload", variant=filename.rsplit(".", 1)[-1].lower()[:8])


def recognize_plate(camera_id: str, variant: str = "normal") -> dict:
    """抓拍单帧 → 双通道并行识别 → 仲裁。返回标准结果结构。每次调用真实执行。"""
    base = {"plate": None, "source": "failed", "ocr": None, "vlm": None,
            "confidence": None, "camera_id": camera_id, "variant": variant,
            "origin": "camera", "image_path": None, "elapsed_ms": None}

    jpg, err = _capture(camera_id, variant)
    if jpg is None:
        return {**base, "error": err, "elapsed_ms": 0}

    image_path = _save_capture(camera_id, variant, jpg)
    return recognize_image_bytes(jpg, image_path=image_path, origin="camera",
                                 camera_id=camera_id, variant=variant)
