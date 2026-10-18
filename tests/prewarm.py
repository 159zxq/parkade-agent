"""链路自检（演示/答辩前跑一次）——不做缓存，只验证现场可达性并校准耗时。

运行（需 mock + 网关在线）：
    python tests/prewarm.py

做两件事：
1. 四台相机抓拍识别各跑一次（检查双通道链路 + 记录真实耗时，供测试报告延迟数据）；
   cam-1 额外跑 blur/occluded 变体，确认演示分支现场可达（blur 的 VLM 通道偶发波动，
   多试几次以确认现场可复现；无缓存，每次都真实识别）；
2. 预跑 10 条演示高频问题走 RAG 检索（暖连接 + 记录耗时）。

输出可截图存 docs/测试证据/ 作为延迟实测数据。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.rag import retrieve  # noqa: E402
from agent.vision import recognize_plate  # noqa: E402

CAMERAS = ["cam-1", "cam-2", "cam-3", "cam-4"]
DEMO_QUESTIONS = [
    "小型车白天怎么收费？",
    "夜间停车便宜多少？",
    "一天最多收多少钱？",
    "停车多久内免费？",
    "月卡多少钱？",
    "怎么预约车位？",
    "预约后最晚什么时候到？",
    "中型车的收费标准是什么？",
    "大型车封顶价是多少？",
    "场内有什么注意事项？",
]


def selfcheck_camera(cam: str) -> int:
    """链路自检一台相机：真实识别一次，记录耗时与来源。"""
    t0 = time.perf_counter()
    r = recognize_plate(cam, "normal")
    ms = int((time.perf_counter() - t0) * 1000)
    ok = r["plate"] is not None
    print(f"  [{'OK' if ok else 'FAIL'}] {cam}: plate={r['plate']} source={r['source']} 耗时 {ms}ms")
    return 0 if ok else 1


def demo_branches() -> int:
    """演示分支现场可达性：blur（VLM 兜底，最多试 3 次）与 occluded（双失败）。"""
    fails = 0
    for attempt in range(1, 4):
        r = recognize_plate("cam-1", "blur")
        if r["source"] == "vlm_only" and r["plate"]:
            print(f"  [OK] blur→vlm_only（第 {attempt} 次，现场该分支可达）")
            break
        print(f"  [..] blur 第 {attempt} 次：{r['source']}（VLM 通道波动，重试）")
    else:
        print("  [WARN] blur 三次未得 vlm_only（现场该分支可能演示为 failed，可接受）")
    r = recognize_plate("cam-1", "occluded")
    ok = r["source"] == "failed" and r["plate"] is None
    print(f"  [{'OK' if ok else 'FAIL'}] occluded→failed 重拍提示分支")
    fails += 0 if ok else 1
    return fails


def main() -> int:
    fails = 0

    print("== 1) 相机识别链路自检（真实识别，无缓存）==")
    for cam in CAMERAS:
        fails += selfcheck_camera(cam)
    print("  —— 演示分支可达性 ——")
    fails += demo_branches()

    print("\n== 2) RAG 检索链路自检（10 条演示问题）==")
    try:
        for q in DEMO_QUESTIONS:
            t0 = time.perf_counter()
            hits = retrieve(q, k=3)
            ms = int((time.perf_counter() - t0) * 1000)
            top = hits[0]["chunk_id"] if hits else "(无)"
            print(f"  [{ms:>4}ms] {q}  → top1={top}")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] 检索自检失败：{e}")
        fails += 1

    print(f"\n{'链路自检完成，现场演示可直接跑' if not fails else f'{fails} 项失败，答辩前必须解决'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
