"""计费引擎 —— 全项目唯一计费实现（契约 docs/接口契约.md §4）。

mock_service 的 /exit 与 agent 工具 calculate_exit_fee 都必须调用本模块；
禁止在任何其他文件重写费率逻辑。费率数字来自 common/rates.yaml（单一事实源）。

规则：
1. 免费 15 分钟：计费区间为 [入场 + free_minutes, 出场]；
2. 计费区间按时间窗切段：day_window 内为标准价，其余为夜间价（标准价 50%）；
3. 每段独立向上取整到 unit_minutes 计费单位；
4. 封顶：按自然日汇总当日各段费用后应用当日封顶，跨天各自封顶再累加；
5. monthly=True（月卡车）总价 0。

自测：python scripts/test_pricing.py
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

RATES_PATH = Path(__file__).resolve().parent / "rates.yaml"
FMT = "%Y-%m-%dT%H:%M:%S"


def load_rates(path: Path | str = RATES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _to_dt(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _next_boundary(moment: datetime, day_start: time, day_end: time) -> datetime:
    """返回 moment 之后最近的分界时刻（07:00 / 22:00 / 00:00）。"""
    t = moment.time()
    if day_start <= t < day_end:                                 # 标准时段 → 今日 22:00
        return datetime.combine(moment.date(), day_end)
    if t >= day_end:                                             # 前半夜 → 次日 00:00
        return datetime.combine(moment.date() + timedelta(days=1), time(0, 0))
    return datetime.combine(moment.date(), day_start)            # 后半夜 → 今日 07:00


def calc_fee(vehicle_type: str, entry_time, exit_time, *, monthly: bool = False) -> dict:
    """返回契约 §3.5 对应字段（不含 plate/area/ticket_id），另附 segments 分段明细。"""
    rates = load_rates()
    if vehicle_type not in rates["vehicles"]:
        raise ValueError(f"未知车型: {vehicle_type!r}，可选: {list(rates['vehicles'])}")
    vehicle = rates["vehicles"][vehicle_type]

    free_minutes = int(rates["free_minutes"])
    unit = int(rates["unit_minutes"])
    day_start = time.fromisoformat(str(rates["day_window"]["start"]))
    day_end = time.fromisoformat(str(rates["day_window"]["end"]))
    cap = float(vehicle["daily_cap"])

    start, end = _to_dt(entry_time), _to_dt(exit_time)
    if end < start:
        raise ValueError("出场时间早于入场时间")

    duration_min = int((end - start).total_seconds() // 60)
    free = min(free_minutes, duration_min)
    billable_minutes = duration_min - free

    segments: list[dict] = []
    cursor = start + timedelta(minutes=free)
    while cursor < end:
        nxt = min(_next_boundary(cursor, day_start, day_end), end)
        minutes = int((nxt - cursor).total_seconds() // 60)
        if minutes <= 0:
            break
        is_night = not (day_start <= cursor.time() < day_end)
        unit_price = float(vehicle["night_rate"] if is_night else vehicle["day_rate"])
        units = math.ceil(minutes / unit)
        segments.append({
            "start": cursor, "end": nxt,
            "window": "night" if is_night else "day",
            "minutes": minutes, "units": units,
            "unit_price": unit_price, "amount": round(units * unit_price, 2),
        })
        cursor = nxt

    day_sums: dict[date, float] = {}
    for seg in segments:
        d = seg["start"].date()
        day_sums[d] = round(day_sums.get(d, 0.0) + seg["amount"], 2)

    subtotal = round(sum(day_sums.values()), 2)
    total = round(sum(min(x, cap) for x in day_sums.values()), 2)
    cap_applied = any(x > cap for x in day_sums.values())
    if monthly:
        total = 0.0

    return {
        "vehicle_type": vehicle_type,
        "entry_time": start.strftime(FMT),
        "exit_time": end.strftime(FMT),
        "duration_min": duration_min,
        "free_minutes": free,
        "billable_minutes": billable_minutes,
        "units": sum(seg["units"] for seg in segments),
        "subtotal": subtotal,
        "cap_applied": cap_applied,
        "total": total,
        "segments": [
            {k: (v.strftime(FMT) if isinstance(v, datetime) else v) for k, v in seg.items()}
            for seg in segments
        ],
    }
