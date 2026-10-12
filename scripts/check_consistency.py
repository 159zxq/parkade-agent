"""费率×语料一致性自动核对（A5b 测试素材，离线可跑，不调 API）。

运行（项目根目录）：python scripts/check_consistency.py

检查四项：
1. rates.yaml → 语料：每个费率数字必须在语料中出现（防"解释与计算打架"）；
2. 编造条款黑名单：未定稿规则的关键词不得出现在语料中；
3. eval_set 标注的 chunk_id 必须真实存在于切块结果中（防标注漂移）；
4. rates.yaml 内部自洽：夜间价 = 标准价 × 50%、封顶/月卡 > 0。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.pricing import load_rates  # noqa: E402
from kb.build_index import CORPUS_DIR, collect_chunks  # noqa: E402

_fails: list[str] = []
_count = 0

# 未定稿规则关键词（出现即视为编造条款）
BLACKLIST = ["会员积分", "积分抵扣", "首小时免费", "节假日翻倍", "洗车券"]


def check(name: str, cond: bool, detail: str = "") -> None:
    global _count
    _count += 1
    print(f"{'[PASS]' if cond else '[FAIL]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def corpus_text() -> tuple[str, dict[str, str]]:
    """返回（全语料归一化文本, {文件名: 归一化文本}）。去掉 markdown 符号与空白。"""
    per_file: dict[str, str] = {}
    for md in sorted(CORPUS_DIR.glob("*.md")):
        if md.name.startswith("_"):
            continue
        text = md.read_text(encoding="utf-8-sig").replace("**", "").replace("`", "")
        per_file[md.name] = "".join(text.split())
    return "".join(per_file.values()), per_file


def fmt(x) -> str:
    return f"{x:g}"


def main() -> int:
    rates = load_rates()
    all_text, per_file = corpus_text()

    # 1) 费率数字 → 语料
    print("== 1) rates.yaml 数字必须出现在语料中 ==")
    vehicles = rates["vehicles"]
    tokens: list[tuple[str, str]] = []
    for vt, v in vehicles.items():
        tokens.append((f"{vt}·标准价 {fmt(v['day_rate'])}元/30分钟", f"{fmt(v['day_rate'])}元/30分钟"))
        tokens.append((f"{vt}·夜间价 {fmt(v['night_rate'])}元/30分钟", f"{fmt(v['night_rate'])}元/30分钟"))
        tokens.append((f"{vt}·封顶 {fmt(v['daily_cap'])}元", f"{fmt(v['daily_cap'])}元"))
        tokens.append((f"{vt}·月卡 {fmt(v['monthly_card'])}元/月", f"{fmt(v['monthly_card'])}元/月"))
    tokens += [
        (f"免费时段 {rates['free_minutes']}分钟", f"{rates['free_minutes']}分钟"),
        (f"计费单位 {rates['unit_minutes']}分钟", f"{rates['unit_minutes']}分钟"),
        ("标准时段起点 07:00", "07:00"),
        ("夜间时段起点 22:00", "22:00"),
        ("夜间比例 50%", "50%"),
        (f"预约保留 {rates['reserve']['keep_minutes']}分钟", f"{rates['reserve']['keep_minutes']}分钟"),
    ]
    for label, tok in tokens:
        where = [f for f, t in per_file.items() if tok in t]
        check(f"{label}", bool(where), f"语料中找不到「{tok}」")

    # 2) 黑名单
    print("\n== 2) 编造条款黑名单 ==")
    for word in BLACKLIST:
        check(f"无「{word}」", word not in all_text, "发现未定稿规则关键词")

    # 3) eval_set id 存在性
    print("\n== 3) eval_set 标注的 chunk_id 必须真实存在 ==")
    real_ids = {c["chunk_id"] for c in collect_chunks()}
    eval_path = ROOT / "kb" / "eval_set.csv"
    bad: list[str] = []
    n_rows = 0
    with open(eval_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            q = (row.get("question") or "").strip()
            if not q:
                continue
            n_rows += 1
            raw = (row.get("expected_chunk_ids") or "").replace("；", "|").replace(";", "|")
            for cid in [x.strip() for x in raw.split("|") if x.strip()]:
                if cid not in real_ids:
                    bad.append(f"{row.get('id', '')}:{cid}")
    check(f"评测集 {n_rows} 行的 expected id 全部存在（共 {len(real_ids)} 个真实 chunk）",
          not bad, f"不存在的标注: {bad}")

    # 4) rates.yaml 内部自洽
    print("\n== 4) rates.yaml 内部自洽 ==")
    for vt, v in vehicles.items():
        ok_night = abs(v["night_rate"] - v["day_rate"] * 0.5) < 1e-9
        check(f"{vt}: 夜间价=标准价×50%", ok_night, f"{v['night_rate']} != {v['day_rate']}×0.5")
        check(f"{vt}: 封顶/月卡 > 0", v["daily_cap"] > 0 and v["monthly_card"] > 0)

    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} 项失败: {_fails}'}（共 {_count} 项）")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
