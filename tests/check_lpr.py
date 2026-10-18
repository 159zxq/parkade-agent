"""合成车牌图规格校准 —— 合成图 → HyperLPR3 识别，打印结果。

用于校准测试抓拍图的绘制规格（底色/字体/字号），即 tests/make_captures.py
的冻结规格依据（260×82 车牌 / 52pt，实测识别 3/3，置信度 0.958–0.981）。
改动 make_captures.py 的绘制参数前先跑本脚本验证识别仍通过。

运行（项目根目录）：python tests/check_lpr.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from make_captures import synth_scene  # noqa: E402


def main() -> int:
    from hyperlpr3 import LicensePlateCatcher

    catcher = LicensePlateCatcher()
    plates = ["京A12345", "京B88888", "沪C66666"]
    ok = 0
    for plate in plates:
        scene = synth_scene("cam-1", "normal", plate=plate)
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
