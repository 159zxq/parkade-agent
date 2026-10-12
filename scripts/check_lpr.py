"""LPR 可行性验证（A0 风险排除）——合成车牌图 → HyperLPR3 识别，打印结果。

这一步决定 B5 的合成图规格（底色/字体/字号）与 A2 的识别链路是否成立。
运行：python scripts/check_lpr.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BLUE = (18, 84, 163)      # 小型车蓝牌底色
WHITE = (245, 245, 245)

# 候选字体（Windows 常见；取第一个存在的）
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到中文字体，请检查 C:/Windows/Fonts")


def synth_plate(plate: str, w: int = 260, h: int = 82) -> Image.Image:
    """合成蓝底白字车牌（居中）。260×82 / 52pt 是实测 HyperLPR3 识别 3/3 的规格，
    改动前先跑本脚本验证。"""
    img = Image.new("RGB", (w, h), BLUE)
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, w - 3, h - 3], outline=WHITE, width=3)  # 白框
    font = pick_font(size=52)
    bbox = d.textbbox((0, 0), plate, font=font)
    d.text(((w - bbox[2] + bbox[0]) / 2, (h - bbox[3] + bbox[1]) / 2), plate,
           font=font, fill=WHITE)
    return img


def synth_scene(plate: str, camera: str = "cam-1", variant: str = "normal") -> Image.Image:
    """640×480 监控场景：灰底 + 中央车牌（位置 (190,199)）+ 右下角水印（B5 按此规格画）。"""
    scene = Image.new("RGB", (640, 480), (150, 150, 148))
    plate_img = synth_plate(plate)
    scene.paste(plate_img, ((640 - plate_img.width) // 2, (480 - plate_img.height) // 2))
    d = ImageDraw.Draw(scene)
    d.text((10, 455), f"{camera} {variant}", font=pick_font(18), fill=(230, 230, 230))
    return scene


def main() -> int:
    from hyperlpr3 import LicensePlateCatcher

    catcher = LicensePlateCatcher()
    plates = ["京A12345", "京B88888", "沪C66666"]
    ok = 0
    for plate in plates:
        scene = synth_scene(plate)
        buf = io.BytesIO()
        scene.save(buf, "JPEG", quality=92)
        arr = np.array(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
        results = catcher(arr)  # [(text, confidence, type, box), ...]
        text = results[0][0].replace(" ", "") if results else "(无结果)"
        conf = f"{results[0][1]:.3f}" if results else "-"
        hit = text == plate
        ok += 1 if hit else 0
        print(f"{'[PASS]' if hit else '[FAIL]'} 合成 {plate} → 识别 {text}（置信度 {conf}）")

    print(f"\n识别成功 {ok}/{len(plates)}")
    return 0 if ok == len(plates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
