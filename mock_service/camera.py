"""相机图合成（成员B 任务卡 B5）。

用 PIL 画 640×480 灰底监控图 → 中央白底黑框车牌 → 右下角水印 "{camera_id} {虚拟时间}"。
支持三种变体（契约 §2）：
  normal   清晰图
  blur     高斯模糊（本地 OCR 大概率失败、VLM 兜底）
  occluded 车牌中部遮挡（两通道都失败，提示重拍）
返回 JPEG 字节流。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from mock_service.clock import clock
from mock_service.state import CAMERAS

WIDTH, HEIGHT = 640, 480

# 优先使用 Windows 微软雅黑，其次常见中文字体，最后退回 PIL 默认位图字体
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_plate_image(camera_id: str, variant: str = "normal") -> bytes:
    """生成指定相机的合成车牌图，返回 JPEG 字节。"""
    cam = CAMERAS[camera_id]
    plate = cam["plate"]

    img = Image.new("RGB", (WIDTH, HEIGHT), (58, 62, 68))  # 监控灰底
    draw = ImageDraw.Draw(img)

    # —— 中央车牌：白底黑框 ——
    plate_w, plate_h = 360, 110
    px = (WIDTH - plate_w) // 2
    py = (HEIGHT - plate_h) // 2
    draw.rectangle([px, py, px + plate_w, py + plate_h], fill=(245, 245, 245),
                   outline=(20, 20, 20), width=5)

    font = _load_font(64)
    # 用 textbbox 居中绘制车牌号
    bbox = draw.textbbox((0, 0), plate, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((px + (plate_w - tw) / 2 - bbox[0], py + (plate_h - th) / 2 - bbox[1]),
              plate, fill=(10, 10, 10), font=font)

    # —— 右下角水印：{camera_id} {虚拟时间} ——
    wm_font = _load_font(24)
    wm_text = f"{camera_id} {clock.now().strftime('%Y-%m-%d %H:%M:%S')}"
    wbbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    ww, wh = wbbox[2] - wbbox[0], wbbox[3] - wbbox[1]
    draw.text((WIDTH - ww - 15, HEIGHT - wh - 15), wm_text, fill=(220, 220, 220), font=wm_font)

    # —— 左上角位置标签（监控感）——
    loc_font = _load_font(20)
    draw.text((15, 12), f"{cam['location']}", fill=(230, 230, 230), font=loc_font)

    # —— 变体处理 ——
    if variant == "blur":
        img = img.filter(ImageFilter.GaussianBlur(6))
    elif variant == "occluded":
        # 用灰块盖住车牌中部约两位字符宽度
        ox = px + plate_w // 2 - 55
        draw = ImageDraw.Draw(img)
        draw.rectangle([ox, py + 8, ox + 110, py + plate_h - 8], fill=(120, 124, 130))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
