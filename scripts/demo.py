"""端到端演示剧本（命令行版，A3/A4 验收；答辩演示的备份通道）。

运行（需 mock 在线；RAG 需已建库；LLM 网关在线）：
    python scripts/demo.py            # 全 8 步
    python scripts/demo.py --steps 1-5  # 只跑前 5 步

8 步剧本（开发方案 §13）：
  1 查车位（工具）          2 抓拍识别（双通道+仲裁）
  3 变体演示（blur→兜底）   4 入场登记（工具+看板）
  5 计费（虚拟时间注入）    6 费率解释（RAG+引用）
  7 域外拒答（无幻觉）      8 多轮指代（记忆）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from agent.graph import build_agent, chat  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

THREAD = "demo-cli"
_fails: list[str] = []


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 60}\n第 {n} 步：{title}\n{'=' * 60}")


def say(agent, msg: str) -> tuple[str, list[str]]:
    print(f"\n[用户] {msg}")
    reply = chat(agent, THREAD, msg)
    print(f"[助手] {reply}")
    return reply, []


def check(name: str, cond: bool) -> None:
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}")
    if not cond:
        _fails.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="1-8", help="如 1-5")
    args = ap.parse_args()
    lo, _, hi = args.steps.partition("-")
    lo, hi = int(lo), int(hi or lo)

    # 环境预检
    try:
        httpx.get(f"{MOCK_BASE}/lots/status", timeout=5)
    except httpx.HTTPError:
        print("[FAIL] mock 服务未启动：uvicorn mock_service.main:app --port 8000")
        return 2
    try:
        httpx.post(f"{MOCK_BASE}/debug/reset", timeout=5)
        print("[环境] mock 已 reset（车位/票据/时钟复位）")
    except httpx.HTTPError:
        pass

    agent = build_agent()

    if lo <= 1 <= hi:
        step(1, "车位查询（工具调用）")
        r, _ = say(agent, "A区现在还有多少车位？")
        check("有车位数据回答（含数字）", bool(re.search(r"\d+", r)) and ("车" in r or "位" in r))

    if lo <= 2 <= hi:
        step(2, "抓拍识别（双通道+仲裁）")
        r, _ = say(agent, "帮我看下 cam-1 相机拍到了什么车。")
        check("识别出车牌 京A12345", "京A12345" in r.replace(" ", ""))

    if lo <= 3 <= hi:
        step(3, "变体演示：blur → VLM 兜底")
        r, _ = say(agent, "那摄像头画面糊了的那张呢？用 cam-1 模糊模式再看一次。")
        # 模型可能不认"模糊模式"，允许引导一轮
        if "京A12345" not in r.replace(" ", ""):
            print("  [提示] 助手未直接走 blur 变体，发送引导……")
            r, _ = say(agent, "请调用工具识别 cam-1，variant 用 blur。")
        print("  [INFO] 本步为演示展示项；blur 分支的可达性已由 prewarm.py 校准")

    if lo <= 4 <= hi:
        step(4, "入场登记（工具+状态变化）")
        # 固定虚拟时间到"今天 10:00"再入场——消除真实时间影响，
        # 使第 5 步 +2 小时后稳定复现契约 §4 样例1（12.00 元）
        today = httpx.get(f"{MOCK_BASE}/debug/time", timeout=10).json()["virtual_time"][:10]
        httpx.post(f"{MOCK_BASE}/debug/time", json={"set": f"{today}T10:00:00"}, timeout=10)
        print(f"  [环境] 虚拟时间已固定为 {today}T10:00:00（演示计费可控）")
        free_before = httpx.get(f"{MOCK_BASE}/lots/status?area=A", timeout=10).json()["areas"][0]["free"]
        # 完整剧本（从 1/2 起跑）有 cam-1 语境；部分运行（--steps 4-x）无前文 → 用明确车牌
        msg = "让刚才识别出的那辆车入场吧。" if lo <= 2 else "请让车牌 京A12345 入场登记。"
        say(agent, msg)
        free_after = httpx.get(f"{MOCK_BASE}/lots/status?area=A", timeout=10).json()["areas"][0]["free"]
        check(f"入场状态生效（A区 {free_before}→{free_before - 1}）",
              free_after == free_before - 1)

    if lo <= 5 <= hi:
        step(5, "计费（虚拟时间注入 +2 小时）")
        httpx.post(f"{MOCK_BASE}/debug/time", json={"offset_minutes": 120}, timeout=10)
        msg = "现在这辆车出场要交多少钱？" if lo <= 2 else "京A12345 现在出场要交多少钱？"
        r, _ = say(agent, msg)
        check("报出费用 12.00 元（契约 §4 样例1）", bool(re.search(r"12\.?0*\s*元", r)))
        check("含计费明细（时长/单位）", ("分钟" in r) or ("单位" in r))

    if lo <= 6 <= hi:
        step(6, "费率解释（RAG 检索 + 引用）——演示灵魂")
        # 预检：知识库是否已建（未建则本步预期降级，不判失败）
        try:
            from agent.rag import retrieve

            pre = retrieve("小型车收费标准", k=1)
            print(f"  [预检] RAG 库就绪：top1={pre[0]['chunk_id']}")
            rag_ready = True
        except Exception as e:  # noqa: BLE001
            print(f"  [预检] RAG 库未就绪（{e}）——本步将降级，跳过判分")
            rag_ready = False
        r, _ = say(agent, "小型车的收费标准规章上具体是怎么规定的？")
        if rag_ready:
            has_cite = bool(re.search(r"\d+\.\d+-\d+", r))
            check("回答含 chunk 引用编号（如 3.2-01）", has_cite)

    if lo <= 7 <= hi:
        step(7, "域外拒答（无幻觉）")
        r, _ = say(agent, "你们停车场老板的手机号是多少？")
        refuse = any(k in r for k in ("无法", "不能", "没有", "抱歉", "不详", "不知道"))
        check("明确拒答/无法提供（不编造号码）", refuse and not re.search(r"1[3-9]\d{9}", r))

    if lo <= 8 <= hi:
        step(8, "多轮指代（上下文保持）")
        r, _ = say(agent, "那刚才那辆车是什么时候进来的？")
        check("结合上下文回答该车（提及车牌或入场时间）",
              ("京A12345" in r.replace(" ", "")) or ("入场" in r) or ("10:" in r))

    print(f"\n{'=' * 60}")
    print(f"{'演示剧本全部通过' if not _fails else f'{len(_fails)} 步未过: {_fails}'}")
    print("=" * 60)
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
