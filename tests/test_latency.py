"""延迟实测（A5b）——对话链路与识别链路分开计账。

运行（需 mock 在线、知识库已建）：
    python tests/test_latency.py                 # 对话 20 轮 + 识别 6 次
    python tests/test_latency.py --dialog 10 --vision 4

输出：min/avg/P50/P95/max 统计 + 逐次明细；存 docs/测试证据/latency_<时间戳>.md。
说明：识别链路含视觉 API（数秒级），与对话链路分开报告是契约与开发方案的既定口径。
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent.graph import build_agent, chat_with_trace  # noqa: E402
from agent.vision import recognize_plate  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

DIALOG_QUESTIONS = [
    "A区还有多少车位？", "小型车白天怎么收费？", "停车多长时间内免费？",
    "夜间收费怎么算？", "一天最多收多少钱？", "怎么预约车位？",
    "预约后最晚什么时候到？", "中型车收费标准是什么？", "大型车封顶价是多少？",
    "场内限速是多少？", "哪些地方不能停车？", "可以开发票吗？",
    "车牌识别失败怎么办？", "月卡多少钱？", "标准时段和夜间时段怎么划分？",
    "停车不足半小时怎么算？", "跨天停车怎么计费？", "同一辆车能预约几个车位？",
    "入场需要取卡吗？", "停车场开放时间？",
]
VISION_CAMS = ["cam-1", "cam-2", "cam-3", "cam-4", "cam-1", "cam-2"]


def stats(name: str, xs: list[float]) -> dict:
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    p50 = xs_sorted[n // 2] if n % 2 else (xs_sorted[n // 2 - 1] + xs_sorted[n // 2]) / 2
    p95 = xs_sorted[min(n - 1, int(round(0.95 * (n - 1))))]
    s = {"name": name, "n": n, "min": min(xs), "avg": statistics.mean(xs),
         "p50": p50, "p95": p95, "max": max(xs)}
    print(f"{name}：n={n} min={s['min']:.2f}s avg={s['avg']:.2f}s "
          f"P50={s['p50']:.2f}s P95={s['p95']:.2f}s max={s['max']:.2f}s")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dialog", type=int, default=20)
    ap.add_argument("--vision", type=int, default=6)
    args = ap.parse_args()

    try:
        httpx.get(f"{MOCK_BASE}/lots/status", timeout=5)
    except httpx.HTTPError:
        print("[FAIL] mock 服务未启动")
        return 2
    httpx.post(f"{MOCK_BASE}/debug/reset", timeout=10)

    ts = time.strftime("%Y%m%d-%H%M%S")
    agent = build_agent()
    detail: list[str] = []

    print(f"== 对话链路（{args.dialog} 轮，含检索+可能的多轮工具调用）==")
    d_times: list[float] = []
    for i, q in enumerate(DIALOG_QUESTIONS[: args.dialog]):
        t0 = time.perf_counter()
        try:
            chat_with_trace(agent, f"lat-{ts}-{i}", q)
            dt = time.perf_counter() - t0
            d_times.append(dt)
            detail.append(f"对话[{i + 1:02d}] {dt:6.2f}s  {q}")
            print(f"  [{i + 1:>2}/{args.dialog}] {dt:5.2f}s  {q}")
        except Exception as e:  # noqa: BLE001
            detail.append(f"对话[{i + 1:02d}]  ERROR  {q}: {e}")
            print(f"  [{i + 1:>2}/{args.dialog}] ERROR {e}")
        time.sleep(1.2)  # 对网关友好，避免限流

    print(f"\n== 识别链路（{args.vision} 次，本地OCR ∥ 视觉API）==")
    v_times: list[float] = []
    v_detail: list[str] = []
    for i in range(min(args.vision, len(VISION_CAMS))):
        cam = VISION_CAMS[i]
        t0 = time.perf_counter()
        r = recognize_plate(cam, "normal")
        dt = time.perf_counter() - t0
        v_times.append(dt)
        line = (f"识别[{i + 1:02d}] {dt:6.2f}s  {cam} → {r['plate']} "
                f"(source={r['source']}, 内部耗时 {r['elapsed_ms']}ms)")
        v_detail.append(line)
        print("  " + line)
        time.sleep(2.5)

    print()
    s_d = stats("对话链路", d_times) if d_times else None
    s_v = stats("识别链路", v_times) if v_times else None

    out_dir = ROOT / "docs" / "测试证据"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"latency_{ts}.md"
    lines = [f"# 延迟实测记录（{datetime.now():%Y-%m-%d %H:%M:%S}）", "",
             "## 统计"]
    for s in (s_d, s_v):
        if s:
            lines.append(f"- {s['name']}：n={s['n']} min={s['min']:.2f}s avg={s['avg']:.2f}s "
                         f"P50={s['p50']:.2f}s P95={s['p95']:.2f}s max={s['max']:.2f}s")
    lines += ["", "## 明细节选", "```", *(detail[:25]), "```",
              "", "```", *v_detail, "```"]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"明细已存：{out.relative_to(ROOT)}")

    ok = bool(s_d) and s_d["avg"] <= 3.0  # 保底口径：对话链路 avg ≤3s
    print(f"\n对话链路均值 {'≤3s（保底口径达成）' if ok else '>3s（需在报告中如实说明并优化）'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
