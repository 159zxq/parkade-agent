"""相机图合成 —— 绘图规格已冻结（实测校准）。

规格来源：`tests/check_lpr.py`（合成图实测 HyperLPR3 识别 3/3，置信度 0.958–0.981），
**改动尺寸/字号/底色前必须先跑 `python tests/check_lpr.py` 验证**：
  - 场景：640×480 灰底（150,150,148）
  - 车牌：260×82，蓝底（18,84,163）白字，白框宽 3，微软雅黑 Bold 52pt
  - 右下角水印：`{camera_id} {虚拟时间}`（监控感）

三种变体：
  normal   清晰图
  blur     整图高斯模糊（本地 OCR 大概率失败、VLM 兜底）
  occluded 车牌中部两位遮挡（两通道都失败，提示重拍）
返回 JPEG 字节流。
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from mock_service.clock import clock
from mock_service.state import CAMERA_TABLE

SCENE_SIZE = (640, 480)
SCENE_BG = (150, 150, 148)
PLATE_SIZE = (260, 82)
PLATE_BLUE = (18, 84, 163)   # 小型车蓝牌底色
PLATE_WHITE = (245, 245, 245)

# 候选字体（Windows 常见；取第一个存在的）——与 check_lpr.py 一致
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def _font_path() -> str:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError("找不到中文字体，请检查 C:/Windows/Fonts")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(), size)


def synth_plate(plate: str, w: int = PLATE_SIZE[0], h: int = PLATE_SIZE[1]) -> Image.Image:
    """合成蓝底白字车牌（居中）。尺寸/字号与 check_lpr.py 冻结规格一致。"""
    img = Image.new("RGB", (w, h), PLATE_BLUE)
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, w - 3, h - 3], outline=PLATE_WHITE, width=3)  # 白框
    font = _font(52)
    bbox = d.textbbox((0, 0), plate, font=font)
    d.text(((w - bbox[2] + bbox[0]) / 2, (h - bbox[3] + bbox[1]) / 2), plate,
           font=font, fill=PLATE_WHITE)
    return img


def synth_scene(camera_id: str, variant: str = "normal") -> Image.Image:
    """640×480 监控场景：灰底 + 中央车牌 + 右下角水印 + 左上角位置标签。"""
    plate = CAMERA_TABLE[camera_id]["plate"]
    scene = Image.new("RGB", SCENE_SIZE, SCENE_BG)

    plate_img = synth_plate(plate)
    px = (SCENE_SIZE[0] - plate_img.width) // 2
    py = (SCENE_SIZE[1] - plate_img.height) // 2
    scene.paste(plate_img, (px, py))

    d = ImageDraw.Draw(scene)
    # 右下角水印：{camera_id} {虚拟时间}
    wm = f"{camera_id} {clock.now().strftime('%Y-%m-%d %H:%M:%S')}"
    wf = _font(18)
    wbox = d.textbbox((0, 0), wm, font=wf)
    d.text((SCENE_SIZE[0] - (wbox[2] - wbox[0]) - 12,
            SCENE_SIZE[1] - (wbox[3] - wbox[1]) - 12), wm, font=wf, fill=(230, 230, 230))
    # 左上角位置标签（监控感）
    d.text((12, 10), CAMERA_TABLE[camera_id]["location"], font=_font(20), fill=(230, 230, 230))

    if variant == "blur":
        scene = scene.filter(ImageFilter.GaussianBlur(6))
    elif variant == "occluded":
        d = ImageDraw.Draw(scene)
        ox = px + plate_img.width // 2 - 55
        d.rectangle([ox, py + 6, ox + 110, py + plate_img.height - 6], fill=SCENE_BG)
    return scene


def render_capture(camera_id: str, variant: str = "normal", quality: int = 92) -> bytes:
    """渲染指定相机、变体的抓拍图，返回 JPEG 字节。"""
    scene = synth_scene(camera_id, variant)
    buf = io.BytesIO()
    scene.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
