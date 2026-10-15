"""Mock 服务共享常量与状态。

- 常量表（相机映射、初始占用）来自成员A 的 stub，**不要改**，与契约 §2/§5 保持一致。
- 成员B 在其上引入可变状态：占用车位副本、票据表、预约表，并支持 /debug/reset 恢复初始。
- 2026-10-14（契约 v1.3）：进出场/缴费/预约每次变更同步写入落盘记录库（mock_service/db.py）；
  进程重启时 `restore_from_db()` 恢复在场票据与未过期预约——根治"车已进场、
  重启后查不到（被说成没进场）"的问题。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from mock_service.clock import clock
from mock_service.db import db

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
    paid_amount: float = 0.0   # 已结清金额（v1.6：出场前须结清当前停车费）


@dataclass
class Reservation:
    reserve_id: str
    plate: str
    area: str | None
    reserve_time: dt.datetime
    keep_until: dt.datetime
    status: str = "active"


@dataclass
class Payment:
    """缴费记录（v1.5）：type=spot 现缴（当场付清）/ monthly 月卡（有效期内出场免计费）。"""

    payment_id: str
    plate: str
    ptype: str                      # spot | monthly
    amount: float
    pay_time: dt.datetime
    valid_until: dt.datetime | None = None   # 仅月卡：有效期截止


@dataclass
class State:
    """可变状态：占用车位 / 票据 / 预约 / 区域总车位（可调试）。

    内存为运行期权威副本；票据与预约的每次变更同步落盘（db），
    进程重启由 `restore_from_db()` 恢复（初始驻车车牌无票据，不落库）。
    """

    occupied: dict[str, str] = field(default_factory=dict)              # plate -> area
    tickets: dict[str, Ticket] = field(default_factory=dict)            # plate -> Ticket
    reservations: dict[str, Reservation] = field(default_factory=dict)  # plate -> Reservation
    payments: dict[str, Payment] = field(default_factory=dict)          # payment_id -> Payment
    capacity: dict[str, int] = field(default_factory=dict)              # area -> 总车位
    _ticket_seq: int = 0
    _reserve_seq: int = 0
    _payment_seq: int = 0

    def reset(self) -> None:
        self.occupied = {}
        self.tickets = {}
        self.reservations = {}
        self.payments = {}
        self.capacity = _default_capacity()
        self._ticket_seq = 0
        self._reserve_seq = 0
        self._payment_seq = 0
        for area, plates in INITIAL_OCCUPIED.items():
            for p in plates:
                self.occupied[normalize_plate(p)] = area

    def restore_from_db(self) -> None:
        """进程启动时从落盘库恢复：在场票据 + 未过期预约 + 有效月卡 + 编号序列。

        应在 `reset()`（重建初始驻车）之后调用；已过期预约落库标记 expired。
        """
        now = clock.now()
        for row in db.load_active_tickets():
            t = Ticket(row["ticket_id"], row["plate"], row["area"],
                       row["vehicle_type"], dt.datetime.fromisoformat(row["entry_time"]),
                       float(row["paid_amount"] or 0))
            self.tickets[row["plate"]] = t
            self.occupied[row["plate"]] = row["area"]
        for row in db.load_active_reservations():
            keep = dt.datetime.fromisoformat(row["keep_until"])
            if keep <= now:
                db.mark_reservation(row["reserve_id"], "expired")
                continue
            self.reservations[row["plate"]] = Reservation(
                row["reserve_id"], row["plate"], row["area"],
                dt.datetime.fromisoformat(row["reserve_time"]), keep)
        for row in db.load_active_monthly(now):
            self.payments[row["payment_id"]] = Payment(
                row["payment_id"], row["plate"], row["ptype"], float(row["amount"]),
                dt.datetime.fromisoformat(row["pay_time"]),
                dt.datetime.fromisoformat(row["valid_until"]))
        self._ticket_seq = db.max_ticket_seq()
        self._reserve_seq = db.max_reserve_seq()
        self._payment_seq = db.max_payment_seq()

    # —— 容量（调试可改，单一事实源默认取 rates.yaml）——
    def capacity_in(self, area: str) -> int:
        return int(self.capacity.get(area, 0))

    def set_capacity(self, area: str, total: int) -> None:
        self.capacity[area] = int(total)

    def set_occupancy(self, area: str, target: int) -> int:
        """调试用：把某区占用数调到 target（0..容量）。

        增：补合成车牌（京DBG{区}{序号}）；
        减：优先移除合成车牌 → 无票据的初始车牌 → 有票据的车牌（连同票据一起移除，
        保持"在场 ⇔ 有票据"不变式；被移除的票据在落盘库中标记 closed）。返回调整后的实际占用数。
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
                ticket = self.tickets.pop(p, None)
                if ticket is not None:  # 调试移除：落盘记录闭合（无结算）
                    db.mark_exited(ticket.ticket_id, clock.now(), None, None, paid=False)
        return self.occupied_in(area)

    # —— 车位统计 ——
    def occupied_in(self, area: str) -> int:
        return sum(1 for a in self.occupied.values() if a == area)

    def reserved_in(self, area: str) -> int:
        """该区被未过期预约保留的位数。调用前先 purge_expired() 保证实时。"""
        return sum(1 for r in self.reservations.values() if r.area == area)

    def free_in(self, area: str, total: int) -> int:
        """对外余位 = 总 − 占用 − 预约保留（普通车辆不可占预约保留位）。"""
        return max(0, total - self.occupied_in(area) - self.reserved_in(area))

    def physical_free_in(self, area: str, total: int) -> int:
        """物理空位 = 总 − 占用（预约车入场用；预约保留位也是"物理空位"）。"""
        return max(0, total - self.occupied_in(area))

    def purge_expired(self) -> None:
        """清掉全部已过期预约（内存 + 落盘标记 expired），保证看板余位实时。"""
        now = clock.now()
        for plate in list(self.reservations):
            r = self.reservations[plate]
            if r.keep_until <= now:
                r.status = "expired"
                del self.reservations[plate]
                db.mark_reservation(r.reserve_id, "expired")

    # —— 票据 ——
    def next_ticket_id(self) -> str:
        self._ticket_seq += 1
        return f"T-{self._ticket_seq:04d}"

    def add_entry(self, plate: str, area: str, vehicle_type: str) -> Ticket:
        ticket = Ticket(self.next_ticket_id(), plate, area, vehicle_type, clock.now())
        self.occupied[plate] = area
        self.tickets[plate] = ticket
        db.save_entry(ticket)   # 进入时间落库
        return ticket

    def remove_entry(self, plate: str, *, exit_time: dt.datetime,
                     duration_min: int | None, fee: float | None) -> Ticket:
        """出场：内存移除 + 落盘记录「离开时间 / 时长 / 费用 / 已缴费」。"""
        ticket = self.tickets.pop(plate)
        self.occupied.pop(plate, None)
        db.mark_exited(ticket.ticket_id, exit_time, duration_min, fee, paid=True)
        return ticket

    # —— 缴费（v1.5：现缴 spot 当场付清 / 月卡 monthly 有效期内免计费）——
    # v1.6：现缴金额入账到当前票据（paid_amount），出场闸机据此校验结清
    def next_payment_id(self) -> str:
        self._payment_seq += 1
        return f"P-{self._payment_seq:04d}"

    def add_payment(self, plate: str, ptype: str, amount: float,
                    valid_until: dt.datetime | None = None) -> Payment:
        pay = Payment(self.next_payment_id(), plate, ptype, float(amount), clock.now(), valid_until)
        self.payments[pay.payment_id] = pay
        db.save_payment(pay.payment_id, pay.plate, pay.ptype, pay.amount,
                        pay.pay_time, pay.valid_until)
        if ptype == "spot":
            ticket = self.tickets.get(plate)
            if ticket is not None:   # 现缴入账当前票据（未结清金额随之减少）
                ticket.paid_amount = round(ticket.paid_amount + pay.amount, 2)
                db.add_ticket_payment(ticket.ticket_id, pay.amount)
        return pay

    def monthly_card(self, plate: str) -> Payment | None:
        """该车牌当前有效的月卡（有效期未过）；无则 None。"""
        now = clock.now()
        for pay in self.payments.values():
            if (pay.plate == plate and pay.ptype == "monthly"
                    and pay.valid_until is not None and pay.valid_until > now):
                return pay
        return None

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
        db.save_reservation(res)   # 预约情况落库
        return res

    def consume_reservation(self, plate: str) -> Reservation | None:
        """入场核销预约（status=consumed；看板保留数随之 −1）。"""
        res = self.reservations.pop(plate, None)
        if res is not None:
            res.status = "consumed"
            db.mark_reservation(res.reserve_id, "consumed")
        return res

    def active_reservation(self, plate: str) -> Reservation | None:
        """未过期预约；已过期则自动释放并返回 None。"""
        res = self.reservations.get(plate)
        if res is None:
            return None
        if res.keep_until <= clock.now():
            res.status = "expired"
            del self.reservations[plate]
            db.mark_reservation(res.reserve_id, "expired")
            return None
        return res


state = State()
state.reset()
