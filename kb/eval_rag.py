"""RAG 命中率评测：读标注测试集 → 逐条检索 top-k → 统计命中率。

用法（在仓库根目录）：
    python kb/eval_rag.py                      # 默认 kb/eval_set.csv，k=3
    python kb/eval_rag.py -k 5                 # 换 k 值

命中定义：expected 中任一 chunk_id 出现在 top-k 内即算命中。
expected 留空的行 = 域外问题（预期拒答），不参与命中率分母。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.rag import retrieve  # noqa: E402


def parse_expected(raw: str) -> list[str]:
    raw = (raw or "").replace("；", "|").replace(";", "|")
    return [x.strip() for x in raw.split("|") if x.strip()]


def read_rows(path: str) -> list[dict]:
    """读评测集，容忍 UTF-8 / GBK 两种保存编码（Excel 另存 CSV 常为 GBK）。"""
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc, newline="") as f:
                out = []
                for r in csv.DictReader(f):
                    r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
                    if r.get("question"):
                        out.append(r)
                return out
        except (UnicodeDecodeError, LookupError) as e:
            last_err = e
    print(f"[FAIL] 无法读取评测集（编码问题：{last_err}），请另存为 UTF-8 后重试")
    raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default=str(ROOT / "kb" / "eval_set.csv"))
    ap.add_argument("-k", type=int, default=3)
    args = ap.parse_args()

    rows = read_rows(args.eval_set)
    if not rows:
        print("[FAIL] 评测集为空")
        return 2

    try:  # 预检：向量库/嵌入是否就绪
        retrieve("预热", k=1)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "未就绪" in msg:
            print(f"[FAIL] {msg}")
        else:
            print(f"[FAIL] 检索预检失败（检查 .env 的 EMBEDDING_* 配置与网络）：{msg}")
        return 2

    hits = total = outside = 0
    by_cat: dict[str, list[int]] = {}
    failures = []
    for row in rows:
        q = row["question"].strip()
        expected = parse_expected(row.get("expected_chunk_ids", ""))
        cat = (row.get("category") or "未分类").strip() or "未分类"
        if not expected:
            outside += 1
            print(f"[域外] {row.get('id', '')} {q}（不参与命中率，预期行为=拒答）")
            continue
        got = retrieve(q, k=args.k)
        got_ids = [c["chunk_id"] for c in got]
        hit = any(e in got_ids for e in expected)
        total += 1
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        if hit:
            hits += 1
            by_cat[cat][0] += 1
        else:
            failures.append((row.get("id", ""), q, expected, got))

    rate = hits / total if total else 0.0
    print(f"\n===== 命中率报告（k={args.k}）=====")
    print(f"总命中率：{hits}/{total} = {rate:.1%}")
    for cat, (h, t) in sorted(by_cat.items()):
        print(f"  {cat:<4} {h}/{t} = {h / t:.0%}" if t else f"  {cat} -")
    if outside:
        print(f"域外问题 {outside} 条（已跳过）")
    if failures:
        print(f"\n未命中 {len(failures)} 条：")
        for cid, q, expected, got in failures:
            top = "，".join(f"{c['chunk_id']}({c['score']})" for c in got)
            print(f"  [{cid}] {q}")
            print(f"      expected: {'|'.join(expected)}")
            print(f"      top{args.k}: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
