"""计费引擎自测（契约 §4 工作示例 + 边界）。运行：python tests/test_pricing.py"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.pricing import calc_fee  # noqa: E402

D = datetime.fromisoformat

# (说明, 车型, 入场, 出场, monthly, 期望total, 期望units, 期望cap_applied)
CASES = [
    ("样例1：白天 2 小时（契约 §4）", "小型车", "2026-11-10T10:00:00", "2026-11-10T12:00:00", False, 12.0, 4, False),
    ("样例2：跨入夜间时段", "小型车", "2026-11-10T21:30:00", "2026-11-10T23:10:00", False, 7.5, 4, False),
    ("样例3：跨天（次晨出场）", "小型车", "2026-11-10T21:00:00", "2026-11-11T01:00:00", False, 15.0, 8, False),
    ("样例4：触当日封顶", "小型车", "2026-11-10T07:00:00", "2026-11-10T23:00:00", False, 30.0, 32, True),
    ("15 分钟内免费", "小型车", "2026-11-10T10:00:00", "2026-11-10T10:15:00", False, 0.0, 0, False),
    ("超 15 分钟收一个计费单位", "小型车", "2026-11-10T10:00:00", "2026-11-10T10:16:00", False, 3.0, 1, False),
    ("中型车白天 2 小时", "中型车", "2026-11-10T10:00:00", "2026-11-10T12:00:00", False, 20.0, 4, False),
    ("月卡总价 0", "小型车", "2026-11-10T10:00:00", "2026-11-10T15:00:00", True, 0.0, 10, False),
]


def main() -> int:
    failed = 0
    for name, vt, entry, exit_, monthly, exp_total, exp_units, exp_cap in CASES:
        fee = calc_fee(vt, D(entry), D(exit_), monthly=monthly)
        ok = (fee["total"] == exp_total and fee["units"] == exp_units
              and fee["cap_applied"] is exp_cap)
        mark = "[PASS]" if ok else "[FAIL]"
        print(f"{mark} {name}: total={fee['total']} units={fee['units']} cap={fee['cap_applied']}"
              f"（期望 total={exp_total} units={exp_units} cap={exp_cap}）")
        failed += 0 if ok else 1

    try:
        calc_fee("三轮车", D("2026-11-10T10:00:00"), D("2026-11-10T11:00:00"))
    except ValueError:
        print("[PASS] 未知车型给出 ValueError（B 映射为 INVALID_PARAM）")
    else:
        print("[FAIL] 未知车型未报错")
        failed += 1

    total_cases = len(CASES) + 1
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} 个用例失败'}（共 {total_cases} 项）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
