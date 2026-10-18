"""测试抓拍图生成（fixture）—— 为 mock 相机准备测试图片。

真实停车场里画面由相机设备侧产出；mock 服务只负责下发——`GET /camera/{id}/capture`
直接读取本脚本生成的 `data/captures/{相机}_{变体}.jpg`（缺图时接口会提示先生成）。
可把自己的实拍图按同名放入 `data/captures/` 替换（已存在则不覆盖，`--force` 强制重生成）。

用法（项目根目录）：
    python tests/make_captures.py            # 生成缺失的图片
    python tests/make_captures.py --force    # 全部重新生成（4 相机 × 3 变体 = 12 张）

绘制规格与 tests/check_lpr.py 的冻结规格一致（260×82 车牌 / 52pt，HyperLPR3 实测 3/3）；
三个变体对应识别仲裁的分支演示：
    normal   清晰（OCR 必成功）
    blur     整图高斯模糊（OCR 必失败 → VLM 兜底）
    occluded 车牌中段遮挡（双通道失败 → 提示重拍）
改动绘制参数前先跑 python tests/check_lpr.py 验证。
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from mock_service.state import CAMERA_TABLE, CAMERA_VARIANTS  # noqa: E402

CAPTURE_DIR = ROOT / "data" / "captures"

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
    """合成蓝底白字车牌（居中）。尺寸/字号为 check_lpr.py 冻结规格。"""
    img = Image.new("RGB", (w, h), PLATE_BLUE)
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, w - 3, h - 3], outline=PLATE_WHITE, width=3)  # 白框
    font = _font(52)
    bbox = d.textbbox((0, 0), plate, font=font)
    d.text(((w - bbox[2] + bbox[0]) / 2, (h - bbox[3] + bbox[1]) / 2), plate,
           font=font, fill=PLATE_WHITE)
    return img


def synth_scene(camera_id: str, variant: str = "normal",
                plate: str | None = None) -> Image.Image:
    """640×480 监控场景：灰底 + 中央车牌 + 左上位置标签 + 右下角「相机号+时间」水印。

    plate 缺省取相机的固定车牌（正常生成路径）；显式传入用于规格校准
    （tests/check_lpr.py 逐个试识别用）。
    """
    plate = plate or CAMERA_TABLE[camera_id]["plate"]
    scene = Image.new("RGB", SCENE_SIZE, SCENE_BG)

    plate_img = synth_plate(plate)
    px = (SCENE_SIZE[0] - plate_img.width) // 2
    py = (SCENE_SIZE[1] - plate_img.height) // 2
    scene.paste(plate_img, (px, py))

    d = ImageDraw.Draw(scene)
    # 右下角水印：{相机号} {生成时间}（监控画面感）
    wm = f"{camera_id} {datetime.now():%Y-%m-%d %H:%M:%S}"
    wf = _font(18)
    wbox = d.textbbox((0, 0), wm, font=wf)
    d.text((SCENE_SIZE[0] - (wbox[2] - wbox[0]) - 12,
            SCENE_SIZE[1] - (wbox[3] - wbox[1]) - 12), wm, font=wf, fill=(230, 230, 230))
    # 左上角位置标签
    d.text((12, 10), CAMERA_TABLE[camera_id]["location"], font=_font(20), fill=(230, 230, 230))

    if variant == "blur":
        scene = scene.filter(ImageFilter.GaussianBlur(6))
    elif variant == "occluded":
        d = ImageDraw.Draw(scene)
        ox = px + plate_img.width // 2 - 55
        d.rectangle([ox, py + 6, ox + 110, py + plate_img.height - 6], fill=SCENE_BG)
    return scene


def render_capture(camera_id: str, variant: str = "normal", quality: int = 92) -> bytes:
    """渲染指定相机、变体的测试抓拍图，返回 JPEG 字节。"""
    buf = io.BytesIO()
    synth_scene(camera_id, variant).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def ensure_captures(force: bool = False) -> list[str]:
    """生成缺失的测试抓拍图（force=True 全部重生成）。返回本次生成的文件名列表。"""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    for cam in CAMERA_TABLE:
        for variant in CAMERA_VARIANTS:
            path = CAPTURE_DIR / f"{cam}_{variant}.jpg"
            if path.exists() and not force:
                continue
            path.write_bytes(render_capture(cam, variant))
            made.append(path.name)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 mock 相机的测试抓拍图（data/captures/）")
    ap.add_argument("--force", action="store_true", help="全部重新生成（默认仅补缺失）")
    args = ap.parse_args()

    made = ensure_captures(force=args.force)
    total = len(list(CAPTURE_DIR.glob("*.jpg")))
    print(f"输出目录：{CAPTURE_DIR.as_posix()}")
    print(f"本次生成 {len(made)} 张{'' if made else '（已全部存在，未变更；--force 可强制重生成）'}")
    print(f"当前共 {total} 张（4 相机 × 3 变体 = 12；mock 的 /camera/{{id}}/capture 直接下发）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
