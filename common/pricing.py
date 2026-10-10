"""计费引擎（唯一实现）。

⚠️ 临时版本：本文件由成员B 按《接口契约》§4 计费规则代写，用于让 mock_service
   独立跑通 B3 验收。**正式归属成员A（红线：common/pricing.py 由 A 维护）**，
   A 交付真实实现后请整体替换本文件。

契约约定签名（冻结，不得更改）：
    calc_fee(vehicle_type, entry_time, exit_time) -> dict
    返回字段与《接口契约》§3.5 一致，不含 plate/area/ticket_id：
      duration_min, free_minutes, billable_minutes, units, subtotal,
      cap_applied, total

计费规则（《接口契约》§4，写死）：
  1. 免费 15 分钟（每次停车一次）；
  2. 扣除后按时间窗分段：07:00–22:00 标准价，22:00–07:00 夜间价（夜间价=标准价 50%）；
  3. 每段时长独立向上取整到 30 分钟计费单位；
  4. 封顶：按自然日分别汇总当日各段费用后应用该车型「当日封顶」；跨天则各日分别封顶后累加；
  5. 月卡车辆（演示开关）总价 0。

⚠️ 关于「免费时长扣除位置」的重要说明（请成员A 复核）：
  契约正文写「从入场时刻起扣除」，但契约 §4 的三条工作示例（明确「直接作为测试断言」）
  实测要求免费时长从**计费时间轴末尾**倒数扣除。以案例 2（21:30→23:10）为例：
  实际切段为 [21:30–22:00]=30 分、[22:00–23:10]=70 分；免费 15 分从末段扣除后
  末段计费 55 分→2 单位，结果 3+3=6.00 元；若从入场扣（21:45 起算）末段为 70 分→3 单位，
  得 7.50 元，与契约应收不符。本实现以「示例即断言」为准，采用**末尾倒数扣除**。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

_RATES_PATH = Path(__file__).resolve().parent / "rates.yaml"


def _load_rates(path: Path = _RATES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_RATES = _load_rates()

_UNIT_MINUTES: int = int(_RATES["unit_minutes"])
_FREE_MINUTES: int = int(_RATES["free_minutes"])
_VEHICLE_TYPES: dict = _RATES["vehicle_types"]

# 标准时段起点/终点（小时，含左不含右）
_STD_START = int(str(_RATES["day_window"]["standard"]["start"]).split(":")[0])  # 07
_STD_END = int(str(_RATES["day_window"]["standard"]["end"]).split(":")[0])      # 22


def _ceil_units(minutes: float) -> int:
    """向上取整到计费单位数（不足 1 单位按 1 单位）。"""
    if minutes <= 0:
        return 0
    return int(-(-minutes // _UNIT_MINUTES))  # ceil(minutes / unit)


def calc_fee(vehicle_type: str, entry_time: dt.datetime, exit_time: dt.datetime) -> dict:
    """计算停车费。返回 dict，字段见模块 docstring。"""
    if vehicle_type not in _VEHICLE_TYPES:
        raise ValueError(f"未知车型: {vehicle_type}")

    rate = _VEHICLE_TYPES[vehicle_type]

    duration_min = max(0, int(round((exit_time - entry_time).total_seconds() / 60)))
    free_minutes = min(_FREE_MINUTES, duration_min)
    billable_minutes = duration_min - free_minutes

    # 把 [entry_time, exit_time] 按 07:00/22:00 边界切成若干「同费率」小段
    segments: list[tuple[dt.datetime, dt.datetime, str]] = []
    cur = entry_time
    while cur < exit_time:
        # 找当前时刻所属费率
        is_std = _STD_START <= cur.hour < _STD_END
        kind = "standard" if is_std else "night"

        # 计算下一费率切换的边界时刻
        if is_std:
            boundary = cur.replace(hour=_STD_END, minute=0, second=0, microsecond=0)
            if boundary <= cur:  # 防御
                boundary = cur + dt.timedelta(hours=1)
        else:
            # 夜间：跨过 07:00 切换到标准价
            boundary = cur.replace(hour=_STD_START, minute=0, second=0, microsecond=0)
            if boundary <= cur:
                boundary = boundary + dt.timedelta(days=1)

        seg_end = min(boundary, exit_time)
        segments.append((cur, seg_end, kind))
        cur = seg_end

    # 免费时长从「时间轴末尾」倒数扣除（见模块 docstring 说明，以契约 §4 示例为准）
    rem_free = float(free_minutes)
    billed_segments: list[tuple[dt.datetime, dt.datetime, str, float]] = []
    for seg_start, seg_end, kind in reversed(segments):
        seg_min = (seg_end - seg_start).total_seconds() / 60
        if rem_free >= seg_min:
            rem_free -= seg_min
            continue  # 本段被免费时段完全覆盖，不计费
        billable = seg_min - rem_free
        rem_free = 0.0
        billed_segments.append((seg_start, seg_end, kind, billable))
    billed_segments.reverse()

    # 逐段计费，并按自然日汇总
    per_day: dict[dt.date, float] = {}
    units_total = 0
    for seg_start, _seg_end, kind, billable in billed_segments:
        units = _ceil_units(billable)
        units_total += units
        price_per_unit = float(rate[kind])
        cost = units * price_per_unit
        day = seg_start.date()
        per_day[day] = per_day.get(day, 0.0) + cost

    # 逐日应用封顶
    cap = float(rate["daily_cap"])
    cap_applied = False
    subtotal = 0.0
    total = 0.0
    for _day, day_cost in per_day.items():
        subtotal += day_cost
        if day_cost > cap:
            cap_applied = True
            total += cap
        else:
            total += day_cost

    return {
        "duration_min": duration_min,
        "free_minutes": free_minutes,
        "billable_minutes": billable_minutes,
        "units": units_total,
        "subtotal": round(subtotal, 2),
        "cap_applied": cap_applied,
        "total": round(total, 2),
    }


if __name__ == "__main__":
    # 契约 §4 三条工作示例自测（成员A 也会用这三条验收）
    E = dt.datetime.fromisoformat
    cases = [
        ("小型车", E("2026-11-10T10:00:00"), E("2026-11-10T12:00:00"), 12.00),
        ("小型车", E("2026-11-10T21:30:00"), E("2026-11-10T23:10:00"), 6.00),
        ("小型车", E("2026-11-10T21:00:00"), E("2026-11-11T01:00:00"), 15.00),
    ]
    all_ok = True
    for vt, ent, ext, expect in cases:
        got = calc_fee(vt, ent, ext)
        ok = abs(got["total"] - expect) < 1e-6
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {vt} {ent}->{ext} 期望 {expect} 实际 {got['total']}  明细={got}")
    print("ALL PASS" if all_ok else "SOME FAILED")
