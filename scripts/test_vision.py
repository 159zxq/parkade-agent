"""双通道识别测试 —— 契约 §6.3 仲裁矩阵 + 实拍三变体（A2 验收）。

运行（需 mock 服务在线：uvicorn mock_service.main:app --port 8000）：
    python scripts/test_vision.py          # 全量：仲裁单测 + 实拍
    python scripts/test_vision.py --unit   # 仅离线仲裁单测（不联网）

实拍预期（三变体的真实分支以本次输出为准，记录到 docs/测试证据/）：
    normal   → both_agree 或 ocr_only（OCR 必成功；VLM 视服务波动）
    blur     → vlm_only 或 failed（OCR 必失败；VLM 偶发波动）
    occluded → failed（双通道均失败 → 重拍提示）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.vision import arbitrate, recognize_plate  # noqa: E402

_fails: list[str] = []
_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _count
    _count += 1
    print(f"{'[PASS]' if cond else '[FAIL]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def unit_tests() -> None:
    """仲裁矩阵五分支（TC-识别-01..05）。"""
    o = {"plate": "京A12345", "confidence": 0.96}
    o2 = {"plate": "京A12346", "confidence": 0.90}
    v = {"plate": "京A12345", "raw": "京A12345"}
    v2 = {"plate": "京A54321", "raw": "京A54321"}

    s, p, c = arbitrate(o, v)
    check("TC-识别-01 双成功且一致 → both_agree", s == "both_agree" and p == "京A12345", f"{s}/{p}")

    s, p, _ = arbitrate(o, v2)
    check("TC-识别-02 双成功不一致 → both_conflict 且取 OCR", s == "both_conflict" and p == "京A12345", f"{s}/{p}")

    s, p, _ = arbitrate(o, None)
    check("TC-识别-03 OCR成功+VLM失败 → ocr_only", s == "ocr_only" and p == "京A12345", f"{s}/{p}")

    s, p, _ = arbitrate(None, v)
    check("TC-识别-04 OCR失败+VLM成功 → vlm_only 兜底", s == "vlm_only" and p == "京A12345", f"{s}/{p}")

    s, p, _ = arbitrate(None, None)
    check("TC-识别-05 双失败 → failed", s == "failed" and p is None, f"{s}/{p}")

    # 置信度语义：vlm_only 无置信度（视觉模型不给数值）
    _, _, c4 = arbitrate(None, v)
    check("TC-识别-04b vlm_only 置信度为 None（不伪造）", c4 is None, str(c4))


def live_tests() -> None:
    """实拍三变体（TC-识别-10..15）。"""
    # normal：OCR 必成功（B 的图已实测 3/3）
    r = recognize_plate("cam-1", "normal")
    check("TC-识别-10 cam-1 normal：plate=京A12345 且 OCR 通道有值",
          r["plate"] == "京A12345" and r["ocr"] is not None,
          str({k: r[k] for k in ("plate", "source", "ocr", "vlm")}))
    check("TC-识别-10b source ∈ {both_agree, ocr_only, both_conflict}",
          r["source"] in ("both_agree", "ocr_only", "both_conflict"), r["source"])
    print(f"      实拍详情：source={r['source']} ocr={r['ocr']} "
          f"vlm={(r['vlm'] or {}).get('plate') if r['vlm'] else None} "
          f"elapsed={r['elapsed_ms']}ms")

    # blur：OCR 必失败（blur=6 实测 7ms 返回 None）；VLM 视服务波动（vlm_only 或 failed）
    rb = recognize_plate("cam-1", "blur")
    check("TC-识别-15 blur：OCR 通道失败（VLM 兜底或双失败）",
          rb["ocr"] is None and rb["source"] in ("vlm_only", "failed"),
          f"ocr={rb['ocr']} source={rb['source']}")
    print(f"      实拍详情 blur：source={rb['source']} plate={rb['plate']} "
          f"vlm={(rb['vlm'] or {}).get('plate') if rb['vlm'] else None} "
          f"elapsed={rb['elapsed_ms']}ms（演示锚点：vlm_only=VLM兜底走近路）")

    ro = recognize_plate("cam-1", "occluded")
    check("TC-识别-13 occluded 双通道失败 → failed 且提示重拍",
          ro["source"] == "failed" and ro["plate"] is None and "重拍" in (ro.get("error") or ""),
          str({k: ro[k] for k in ("source", "plate", "error")}))

    # 未知相机
    rx = recognize_plate("cam-9", "normal")
    check("TC-识别-14 未知相机 → failed + 明确错误",
          rx["source"] == "failed" and "未知相机" in (rx.get("error") or ""), str(rx.get("error")))


def main() -> int:
    only_unit = "--unit" in sys.argv
    unit_tests()
    if not only_unit:
        print("\n—— 实拍（需 mock 服务在线）——")
        try:
            live_tests()
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 实拍阶段异常（mock 未启动？）：{e}")
            _fails.append("实拍阶段")

    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} 项失败: {_fails}'}（共 {_count} 项）")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
