"""韧性测试（A5b）——持久化复验 + 异常流（mock 停服 / 重复入场）。

运行（需 mock 在线）：python scripts/test_resilience.py

覆盖测试方案 §8 的「持久化」「异常流」两行：
1. Chroma 持久化：两个独立子进程执行同一检索，chunk 序列一致（跨进程重载生效）；
2. Agent 记忆持久化：子进程 phase1 写入 → 另一子进程 phase2 复述（SqliteSaver 落盘）；
3. mock 停服韧性：子进程（MOCK_BASE 指向不可达端口）验证 工具 / 识别 / Agent 回合
   均给出结构化错误或可读提示，全程不抛异常；
4. 重复入场：同一车牌二次入场 → ALREADY_INSIDE（业务异常流）。

证据存 docs/测试证据/resilience_<时间戳>.md（本地留存，报告精选引用）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from common.config import MOCK_BASE  # noqa: E402

PY = sys.executable
DEAD_BASE = "http://127.0.0.1:9"  # 保留端口，连接立即被拒 → 模拟"服务不可达"

_fails: list[str] = []
_warns: list[str] = []
_out_parts: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def warn(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'[PASS]' if cond else '[WARN]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _warns.append(name)


def run(args: list[str], *, env_extra: dict | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # Windows 子进程输出统一 UTF-8
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PY, *args], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)


def record(title: str, text: str) -> None:
    _out_parts.append(f"## {title}\n\n```\n{text[-1500:]}\n```\n")


def main() -> int:
    try:
        assert httpx.get(f"{MOCK_BASE}/lots/status", timeout=5).status_code == 200
    except (httpx.HTTPError, AssertionError):
        print("[FAIL] mock 服务未启动：uvicorn mock_service.main:app --port 8000")
        return 2

    ts = time.strftime("%Y%m%d-%H%M%S")

    # ── 1) Chroma 持久化 ─────────────────────────────────────────────
    print("== 1) Chroma 持久化（跨进程检索一致）==")
    snippet = ("import json,sys; sys.path.insert(0,'.'); "
               "from agent.rag import retrieve; "
               "print('IDS:'+json.dumps([h['chunk_id'] for h in retrieve('小型车白天怎么收费',3)]))")
    ids_runs: list[list[str]] = []
    for i in (1, 2):
        p = run(["-c", snippet], timeout=120)
        line = next((l for l in p.stdout.splitlines() if l.startswith("IDS:")), "")
        ids_runs.append(json.loads(line[4:]) if line else [])
        record(f"Chroma 进程{i}", p.stdout + p.stderr)
    check("检索非空（库已建）", bool(ids_runs[0]), str(ids_runs[0]))
    check("两个独立进程检索结果一致", bool(ids_runs[0]) and ids_runs[0] == ids_runs[1], str(ids_runs))

    # ── 2) Agent 记忆持久化 ──────────────────────────────────────────
    print("\n== 2) Agent 记忆持久化（SqliteSaver 跨进程）==")
    db = ROOT / "data" / "checkpoints.sqlite"
    try:
        db.unlink()
        print("  （已清空旧记忆库，确保结论干净）")
    except OSError:
        pass
    httpx.post(f"{MOCK_BASE}/debug/reset", timeout=10)
    p1 = run(["scripts/check_agent.py", "--phase", "1"], timeout=300)
    record("phase1（进程1：两轮对话写入记忆）", p1.stdout + p1.stderr)
    check("phase1（新进程完成两轮对话）", p1.returncode == 0, p1.stdout[-400:])
    p2 = run(["scripts/check_agent.py", "--phase", "2"], timeout=240)
    record("phase2（进程2：复述上一进程的记忆）", p2.stdout + p2.stderr)
    check("phase2（另一进程复述记忆成功）", p2.returncode == 0, p2.stdout[-400:])

    # ── 3) mock 停服韧性 ─────────────────────────────────────────────
    print("\n== 3) mock 停服韧性（MOCK_BASE 指向不可达端口）==")
    dead_env = {"MOCK_BASE": DEAD_BASE}
    snip_tools = (
        "import json,sys; sys.path.insert(0,'.'); "
        "from agent.tools import query_parking_status, register_entry, calculate_exit_fee; "
        "from agent.vision import recognize_plate; "
        "r={}; r['q']=query_parking_status.invoke({}); r['e']=register_entry.invoke({'plate':'京TEST99'}); "
        "r['f']=calculate_exit_fee.invoke({'plate':'京TEST99'}); r['v']=recognize_plate('cam-1'); "
        "print('RES:'+json.dumps(r, ensure_ascii=False))"
    )
    p = run(["-c", snip_tools], env_extra=dead_env, timeout=180)
    record("停服·工具与识别", p.stdout + p.stderr)
    res: dict = {}
    line = next((l for l in p.stdout.splitlines() if l.startswith("RES:")), "")
    if line:
        res = json.loads(line[4:])
    check("工具调用返回结构化错误（不抛异常）",
          bool(res) and all((res.get(k) or {}).get("ok") is False for k in ("q", "e", "f")),
          str(res)[:300])
    codes = {(res.get(k) or {}).get("error", {}).get("code") for k in ("q", "e", "f")}
    check("错误码均为 MOCK_UNREACHABLE", codes == {"MOCK_UNREACHABLE"}, str(codes))
    v = res.get("v") or {}
    check("识别工具 failed + 明确错误提示",
          v.get("source") == "failed" and "不可达" in (v.get("error") or ""),
          str({k: v.get(k) for k in ("source", "error")}))

    snip_agent = (
        "import sys; sys.path.insert(0,'.'); "
        "from agent.graph import build_agent, chat_with_trace; "
        "agent=build_agent(); reply,_=chat_with_trace(agent,'resilience-dead','A区还有多少车位？'); "
        "print('REPLY:'+reply.replace(chr(10),' ')[:300])"
    )
    p = run(["-c", snip_agent], env_extra=dead_env, timeout=300)
    record("停服·Agent 回合", p.stdout + p.stderr)
    reply = next((l[6:] for l in p.stdout.splitlines() if l.startswith("REPLY:")), "")
    check("Agent 回合不崩溃（正常结束）", p.returncode == 0, (p.stdout[-400:] + p.stderr[-200:]))
    warn("失败提示可读（面向用户而非堆栈）",
         any(k in reply for k in ("不可达", "无法", "失败", "服务", "连接", "稍后", "重试", "抱歉", "维护")),
         reply[:200])

    # ── 4) 重复入场（业务异常流）─────────────────────────────────────
    print("\n== 4) 重复入场（业务异常流）==")
    httpx.post(f"{MOCK_BASE}/debug/reset", timeout=10)
    from agent.tools import register_entry  # noqa: E402  延迟导入：这段走在线 MOCK_BASE
    r1 = register_entry.invoke({"plate": "京RES001"})
    r2 = register_entry.invoke({"plate": "京RES001"})
    check("首次入场 ok:true", (r1 or {}).get("ok") is True, str(r1)[:200])
    check("二次入场 ALREADY_INSIDE",
          (r2 or {}).get("ok") is False and ((r2 or {}).get("error") or {}).get("code") == "ALREADY_INSIDE",
          str(r2)[:200])
    httpx.post(f"{MOCK_BASE}/debug/reset", timeout=10)

    # ── 证据与汇总 ──────────────────────────────────────────────────
    out_dir = ROOT / "docs" / "测试证据"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"resilience_{ts}.md"
    lines = [f"# 韧性测试记录（{datetime.now():%Y-%m-%d %H:%M:%S}）", "",
             f"FAIL {len(_fails)}｜WARN {len(_warns)}", "",
             *(_out_parts)]
    if _fails:
        lines.append(f"\n**未通过**：{_fails}")
    if _warns:
        lines.append(f"\n**提示**：{_warns}")
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{'=' * 58}")
    print(f"校验结果：{'全部通过' if not _fails else f'{len(_fails)} 项失败: {_fails}'}"
          + (f"（另有 {len(_warns)} 项提示）" if _warns else ""))
    print(f"证据：{out.relative_to(ROOT)}")
    print("=" * 58)
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
