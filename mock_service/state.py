"""Mock 停车场服务的状态机与数据表（成员B 任务卡 B1/B2/B3/B6）。

数据全部驻内存，POST /debug/reset 可恢复到契约 §5 定义的初始状态。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from mock_service.clock import clock

# —— 契约 §2：相机与车牌映射（固定，保证演示与测试可复现）——
CAMERAS: dict[str, dict] = {
    "cam-1": {"location": "A 区入口", "plate": "京A12345", "vehicle_type": "小型车"},
    "cam-2": {"location": "B 区入口", "plate": "京B88888", "vehicle_type": "小型车"},
    "cam-3": {"location": "C 区入口", "plate": "沪C66666", "vehicle_type": "小型车"},
    "cam-4": {"location": "出口", "plate": "京A12345", "vehicle_type": "小型车"},
}

# —— 契约 §1：区域与容量 ——
AREAS: dict[str, dict] = {
    "A": {"name": "地面区", "total": 30},
    "B": {"name": "地下一层", "total": 60},
    "C": {"name": "地下二层", "total": 60},
}
AREA_ORDER = ["A", "B", "C"]

# —— 契约 §5：初始驻车状态（reset 后）——
INITIAL_PARKED: dict[str, list[str]] = {
    "A": ["京N10001", "京N10002", "京N10003"],
    "B": ["京N20001", "京N20002", "京N20003", "京N20004",
          "京N20005", "京N20006", "京N20007", "京N20008"],
    "C": ["京N30001", "京N30002"],
}

DEFAULT_VEHICLE_TYPE = "小型车"

TIME_FMT = "%Y-%m-%dT%H:%M:%S"

RESERVE_KEEP_MINUTES = 30


def fmt(d: dt.datetime) -> str:
    return d.strftime(TIME_FMT)


def normalize_plate(plate: str) -> str:
    """契约 §0：统一去空格、字母转大写。"""
    return "".join(str(plate).split()).upper()


@dataclass
class Ticket:
    ticket_id: str
    plate: str
    area: str
    vehicle_type: str
    entry_time: dt.datetime


@dataclass
class Reservation:
    reserve_id: str
    plate: str
    area: str | None
    reserve_time: dt.datetime
    keep_until: dt.datetime
    status: str = "active"


@dataclass
class State:
    parked: dict[str, str] = field(default_factory=dict)        # plate -> area
    tickets: dict[str, Ticket] = field(default_factory=dict)    # plate -> Ticket
    reservations: dict[str, Reservation] = field(default_factory=dict)  # plate -> Reservation
    _ticket_seq: int = 0
    _reserve_seq: int = 0

    # —— 初始化 / 重置 ——
    def reset(self) -> None:
        clock.reset()
        self.parked = {}
        self.tickets = {}
        self.reservations = {}
        self._ticket_seq = 0
        self._reserve_seq = 0
        for area, plates in INITIAL_PARKED.items():
            for p in plates:
                self.parked[normalize_plate(p)] = area

    # —— 车位统计 ——
    def area_counts(self) -> list[dict]:
        out = []
        for code in AREA_ORDER:
            total = AREAS[code]["total"]
            occupied = sum(1 for a in self.parked.values() if a == code)
            out.append({
                "area": code,
                "name": AREAS[code]["name"],
                "total": total,
                "free": total - occupied,
                "occupied": occupied,
            })
        return out

    def free_in(self, area: str) -> int:
        total = AREAS[area]["total"]
        occupied = sum(1 for a in self.parked.values() if a == area)
        return total - occupied

    def first_area_with_free(self) -> str | None:
        for code in AREA_ORDER:
            if self.free_in(code) > 0:
                return code
        return None

    # —— 出入场 ——
    def next_ticket_id(self) -> str:
        self._ticket_seq += 1
        return f"T-{self._ticket_seq:04d}"

    def add_entry(self, plate: str, area: str, vehicle_type: str) -> Ticket:
        ticket = Ticket(
            ticket_id=self.next_ticket_id(),
            plate=plate,
            area=area,
            vehicle_type=vehicle_type,
            entry_time=clock.now(),
        )
        self.parked[plate] = area
        self.tickets[plate] = ticket
        return ticket

    def remove_entry(self, plate: str) -> Ticket:
        ticket = self.tickets.pop(plate)
        self.parked.pop(plate, None)
        return ticket

    # —— 预约 ——
    def next_reserve_id(self) -> str:
        self._reserve_seq += 1
        return f"R-{self._reserve_seq:04d}"

    def add_reservation(self, plate: str, area: str | None, reserve_time: dt.datetime) -> Reservation:
        res = Reservation(
            reserve_id=self.next_reserve_id(),
            plate=plate,
            area=area,
            reserve_time=reserve_time,
            keep_until=reserve_time + dt.timedelta(minutes=RESERVE_KEEP_MINUTES),
        )
        self.reservations[plate] = res
        return res

    def active_reservation(self, plate: str) -> Reservation | None:
        """返回未过期（keep_until > now）的有效预约；过期则自动释放。"""
        res = self.reservations.get(plate)
        if res is None:
            return None
        if res.keep_until <= clock.now():
            res.status = "expired"
            del self.reservations[plate]
            return None
        return res


state = State()
state.reset()
