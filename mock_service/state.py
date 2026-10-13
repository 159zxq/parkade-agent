"""Mock 服务共享常量与状态。

- 常量表（相机映射、初始占用）来自成员A 的 stub，**不要改**，与契约 §2/§5 保持一致。
- 成员B 在其上引入可变状态：占用车位副本、票据表、预约表，并支持 /debug/reset 恢复初始。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from mock_service.clock import clock

# 契约 §2：相机 → 车牌映射（固定，保证演示与测试可复现）
CAMERA_TABLE: dict[str, dict] = {
    "cam-1": {"location": "A 区入口", "plate": "京A12345", "vehicle_type": "小型车"},
    "cam-2": {"location": "B 区入口", "plate": "京B88888", "vehicle_type": "小型车"},
    "cam-3": {"location": "C 区入口", "plate": "沪C66666", "vehicle_type": "小型车"},
    "cam-4": {"location": "出口", "plate": "京A12345", "vehicle_type": "小型车"},
}

CAMERA_VARIANTS = ("normal", "blur", "occluded")

# 契约 §5：reset 后的初始驻车状态
INITIAL_OCCUPIED: dict[str, list[str]] = {
    "A": ["京N10001", "京N10002", "京N10003"],
    "B": [f"京N2{i:04d}" for i in range(1, 9)],   # 京N20001 ~ 京N20008
    "C": ["京N30001", "京N30002"],
}

# 契约 §1：区域顺序（无 area 时按 A→B→C 找第一个有空位的区）
AREA_ORDER = ("A", "B", "C")


def _default_capacity() -> dict[str, int]:
    """初始容量取 rates.yaml 的 areas 段（单一事实源；调试可经 /debug/lots 覆盖）。"""
    from common.pricing import load_rates

    return {code: int(meta["total"]) for code, meta in load_rates()["areas"].items()}

DEFAULT_VEHICLE_TYPE = "小型车"
FMT = "%Y-%m-%dT%H:%M:%S"


def normalize_plate(plate: str) -> str:
    """契约 §0：统一去空格、字母转大写。"""
    return "".join(str(plate).split()).upper()


def fmt(d: dt.datetime) -> str:
    return d.strftime(FMT)


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
    """可变状态：占用车位 / 票据 / 预约 / 区域总车位（可调试）。reset() 恢复契约 §5 初始。"""

    occupied: dict[str, str] = field(default_factory=dict)              # plate -> area
    tickets: dict[str, Ticket] = field(default_factory=dict)            # plate -> Ticket
    reservations: dict[str, Reservation] = field(default_factory=dict)  # plate -> Reservation
    capacity: dict[str, int] = field(default_factory=dict)              # area -> 总车位
    _ticket_seq: int = 0
    _reserve_seq: int = 0

    def reset(self) -> None:
        self.occupied = {}
        self.tickets = {}
        self.reservations = {}
        self.capacity = _default_capacity()
        self._ticket_seq = 0
        self._reserve_seq = 0
        for area, plates in INITIAL_OCCUPIED.items():
            for p in plates:
                self.occupied[normalize_plate(p)] = area

    # —— 容量（调试可改，单一事实源默认取 rates.yaml）——
    def capacity_in(self, area: str) -> int:
        return int(self.capacity.get(area, 0))

    def set_capacity(self, area: str, total: int) -> None:
        self.capacity[area] = int(total)

    def set_occupancy(self, area: str, target: int) -> int:
        """调试用：把某区占用数调到 target（0..容量）。

        增：补合成车牌（京DBG{区}{序号}）；
        减：优先移除合成车牌 → 无票据的初始车牌 → 有票据的车牌（连同票据一起移除，
        保持"在场 ⇔ 有票据"不变式）。返回调整后的实际占用数。
        """
        target = max(0, min(int(target), self.capacity_in(area)))
        current = self.occupied_in(area)

        if target > current:
            i = 1
            while self.occupied_in(area) < target:
                p = f"京DBG{area}{i:03d}"
                if p not in self.occupied:
                    self.occupied[p] = area
                i += 1
        elif target < current:
            def sort_key(p: str) -> tuple:
                synthetic = 0 if p.startswith("京DBG") else (1 if p not in self.tickets else 2)
                return (synthetic, p)

            for p in sorted(self.occupied, key=sort_key):
                if self.occupied_in(area) <= target:
                    break
                if self.occupied[p] != area:
                    continue
                del self.occupied[p]
                self.tickets.pop(p, None)
        return self.occupied_in(area)

    # —— 车位统计 ——
    def occupied_in(self, area: str) -> int:
        return sum(1 for a in self.occupied.values() if a == area)

    def free_in(self, area: str, total: int) -> int:
        return max(0, total - self.occupied_in(area))

    # —— 票据 ——
    def next_ticket_id(self) -> str:
        self._ticket_seq += 1
        return f"T-{self._ticket_seq:04d}"

    def add_entry(self, plate: str, area: str, vehicle_type: str) -> Ticket:
        ticket = Ticket(self.next_ticket_id(), plate, area, vehicle_type, clock.now())
        self.occupied[plate] = area
        self.tickets[plate] = ticket
        return ticket

    def remove_entry(self, plate: str) -> Ticket:
        ticket = self.tickets.pop(plate)
        self.occupied.pop(plate, None)
        return ticket

    # —— 预约 ——
    def next_reserve_id(self) -> str:
        self._reserve_seq += 1
        return f"R-{self._reserve_seq:04d}"

    def add_reservation(self, plate: str, area: str | None,
                        reserve_time: dt.datetime, keep_minutes: int) -> Reservation:
        res = Reservation(
            reserve_id=self.next_reserve_id(),
            plate=plate,
            area=area,
            reserve_time=reserve_time,
            keep_until=reserve_time + dt.timedelta(minutes=keep_minutes),
        )
        self.reservations[plate] = res
        return res

    def active_reservation(self, plate: str) -> Reservation | None:
        """未过期预约；已过期则自动释放并返回 None。"""
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
