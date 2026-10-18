"""两轮对话 + 跨进程记忆（重启后历史不丢）。

用法（先启动 mock：uvicorn mock_service.main:app --port 8000）：
    python tests/check_agent.py --phase 1    # 进程1：查车位 → 车牌入场
    python tests/check_agent.py --phase 2    # 进程2：问"刚才登记了哪辆车"（验证落盘）

phase 1 与 phase 2 是两个独立进程，共享 data/checkpoints.sqlite，
phase 2 能答对车牌即证明 SqliteSaver 落盘生效（验证通过）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from agent.graph import build_agent, chat  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

THREAD = "a1-smoke"


def _ensure_mock() -> bool:
    try:
        r = httpx.get(f"{MOCK_BASE}/lots/status", timeout=5)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, choices=[1, 2], required=True)
    args = parser.parse_args()

    if not _ensure_mock():
        print(f"[FAIL] Mock 服务未启动（{MOCK_BASE}）。先运行：uvicorn mock_service.main:app --port 8000")
        return 2

    agent = build_agent()

    if args.phase == 1:
        q1 = "A区现在还有多少车位？"
        print(f"[用户] {q1}")
        a1 = chat(agent, THREAD, q1)
        print(f"[助手] {a1}\n")
        q2 = "好的，请让车牌 京Z99999 入场登记。"
        print(f"[用户] {q2}")
        a2 = chat(agent, THREAD, q2)
        print(f"[助手] {a2}\n")

        ok1 = any(k in a1 for k in ("27", "车位"))
        ok2 = any(k in a2 for k in ("T-", "入场", "登记", "成功"))
        print(f"[{'PASS' if ok1 else 'FAIL'}] 轮1：车位查询有结果")
        print(f"[{'PASS' if ok2 else 'FAIL'}] 轮2：入场登记有回执")
        return 0 if (ok1 and ok2) else 1

    # phase 2：新进程，同一 thread_id
    q = "我刚才让你登记的是哪辆车牌？只回答车牌号。"
    print(f"[用户] {q}")
    a = chat(agent, THREAD, q)
    print(f"[助手] {a}\n")
    ok = "京Z99999" in a
    print(f"[{'PASS' if ok else 'FAIL'}] 跨进程记忆：新进程能回忆起上一进程登记的车牌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
