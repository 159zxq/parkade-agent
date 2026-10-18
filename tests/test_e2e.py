"""端到端情景测试 ——多轮上下文 / 指代消解 / 域外拒答。

运行（需 mock 在线、知识库已建）：
    python tests/test_e2e.py

三个情景（测试报告素材）：
  情景1 业务全流程（6 轮）：车位→抓拍→入场→计费→费率依据引用
  情景2 多轮指代（5 轮）：费率追问链（小型→中型→夜间→封顶→跨天）
  情景3 域外拒答（5 问）：不编造、不越界

逐轮记录存 docs/测试证据/e2e_<时间戳>.md；控制台输出 PASS/FAIL 汇总。
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent.graph import build_agent, chat_with_trace  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

_fails: list[str] = []
_warns: list[str] = []
_log: list[tuple[str, str, str, list[str]]] = []  # (情景, 用户, 回复, 引用chunk)


def check(name: str, cond: bool) -> None:
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}")
    if not cond:
        _fails.append(name)


def warn(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'[PASS]' if cond else '[WARN]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _warns.append(name)


def turn(agent, thread: str, scenario: str, msg: str) -> tuple[str, list[str]]:
    reply, snippets = chat_with_trace(agent, thread, msg)
    ids = [s["chunk_id"] for s in snippets]
    _log.append((scenario, msg, reply, ids))
    print(f"\n[用户] {msg}\n[助手] {reply}")
    return reply, ids


def scenario1(agent, ts: str) -> None:
    print("\n" + "=" * 62 + "\n情景1：业务全流程（6 轮）\n" + "=" * 62)
    thread = f"e2e-s1-{ts}"
    today = httpx.get(f"{MOCK_BASE}/debug/time", timeout=10).json()["virtual_time"][:10]
    httpx.post(f"{MOCK_BASE}/debug/time", json={"set": f"{today}T10:00:00"}, timeout=10)

    r, _ = turn(agent, thread, "情景1", "A区现在还有多少车位？")
    check("1.1 车位查询有数字", bool(re.search(r"\d+", r)))

    r, _ = turn(agent, thread, "情景1", "帮我看下 cam-1 相机拍到了什么车。")
    check("1.2 抓拍识别出 京A12345", "京A12345" in r.replace(" ", ""))

    turn(agent, thread, "情景1", "让这辆车入场吧。")
    free = httpx.get(f"{MOCK_BASE}/lots/status?area=A", timeout=10).json()["areas"][0]["free"]
    check(f"1.3 入场状态生效（A区 free=26，实际 {free}）", free == 26)

    httpx.post(f"{MOCK_BASE}/debug/time", json={"offset_minutes": 120}, timeout=10)
    r, _ = turn(agent, thread, "情景1", "现在这辆车出场要交多少钱？")
    check("1.4 计费 12.00 元（样例1）", bool(re.search(r"12\.?0*\s*元", r)))

    r, _ = turn(agent, thread, "情景1", "小型车的收费标准是哪一条规章规定的？")
    check("1.5 费率解释含规章引用（如 3.2-01）", bool(re.search(r"\d\.\d+-\d+", r)))

    r, _ = turn(agent, thread, "情景1", "感谢，帮我把刚才的入场记录再说一下。")
    warn("1.6 多轮记忆能复述入场信息（车牌或票号）",
         ("京A12345" in r.replace(" ", "")) or ("T-" in r))


def scenario2(agent, ts: str) -> None:
    print("\n" + "=" * 62 + "\n情景2：多轮指代（费率追问链，5 轮）\n" + "=" * 62)
    thread = f"e2e-s2-{ts}"

    r, ids = turn(agent, thread, "情景2", "小型车白天怎么收费？")
    check("2.1 小型车标准价 3 元 + 引用", ("3" in r) and bool(re.search(r"\d\.\d+-\d+", r)))

    r, _ = turn(agent, thread, "情景2", "那中型车呢？")
    check("2.2 指代→中型车标准价 5 元", "5" in r)

    r, _ = turn(agent, thread, "情景2", "晚上呢？")
    check("2.3 追夜间价（1.5 或 50% 出现）", ("1.5" in r) or ("50%" in r))

    r, _ = turn(agent, thread, "情景2", "那它一天封顶多少？")
    check("2.4 指代→中型车封顶 45 元", "45" in r)

    r, _ = turn(agent, thread, "情景2", "如果停车跨过午夜，怎么算？")
    warn("2.5 跨天规则（分别计费/自然日/封顶）",
         any(k in r for k in ("分别", "自然日", "跨天", "封顶")))


REFUSALS = [
    ("3.1 负责人电话（不编造号码）", "你们停车场老板的手机号是多少？"),
    ("3.2 天气话题（不越界）", "今天天气怎么样？"),
    ("3.3 写诗（不越界）", "帮我写一首诗吧。"),
    ("3.4 餐厅推荐（不越界）", "附近有什么好吃的餐厅？"),
    ("3.5 股票推荐（不越界）", "给我推荐几只股票。"),
]
REFUSE_KW = ("无法", "不能", "不提供", "抱歉", "不属", "之外", "只能", "仅能",
             "不涉及", "不便", "没有相关", "不支持", "不负责", "请咨询")


def scenario3(agent, ts: str) -> None:
    print("\n" + "=" * 62 + "\n情景3：域外拒答（5 问，无幻觉）\n" + "=" * 62)
    thread = f"e2e-s3-{ts}"
    for name, q in REFUSALS:
        r, _ = turn(agent, thread, "情景3", q)
        refused = any(k in r for k in REFUSE_KW)
        no_phone = not re.search(r"1[3-9]\d{9}", r)
        check(name, refused and no_phone)


def main() -> int:
    try:
        httpx.get(f"{MOCK_BASE}/lots/status", timeout=5)
    except httpx.HTTPError:
        print("[FAIL] mock 服务未启动：uvicorn mock_service.main:app --port 8000")
        return 2
    httpx.post(f"{MOCK_BASE}/debug/reset", timeout=10)

    ts = time.strftime("%Y%m%d-%H%M%S")
    agent = build_agent()
    t0 = time.perf_counter()
    scenario1(agent, ts)
    scenario2(agent, ts)
    scenario3(agent, ts)
    dur = time.perf_counter() - t0

    # 保存逐轮记录
    out_dir = ROOT / "docs" / "测试证据"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"e2e_{ts}.md"
    lines = [f"# 端到端情景测试记录（{datetime.now():%Y-%m-%d %H:%M:%S}）",
             f"总耗时 {dur:.0f}s｜FAIL {len(_fails)}｜WARN {len(_warns)}", ""]
    for sc, q, a, ids in _log:
        lines += [f"## [{sc}] 用户：{q}", "", a, "",
                  f"引用片段：{' '.join(ids) if ids else '（无）'}", ""]
    if _fails:
        lines.append(f"**未通过**：{_fails}")
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{'=' * 62}")
    print(f"校验结果：{'全部通过' if not _fails else f'{len(_fails)} 项失败: {_fails}'}"
          + (f"（另有 {len(_warns)} 项提示: {_warns}）" if _warns else ""))
    print(f"逐轮记录：{out.relative_to(ROOT)}｜总耗时 {dur:.0f}s")
    print("=" * 62)
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
