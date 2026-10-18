"""Mock 服务落盘记录库（SQLite）—— 进出场 / 缴费 / 预约全留痕。

- 位置：`data/mock.sqlite`（gitignored；与 checkpoints/web.sqlite 同级）；
- `tickets`：一行一票据 —— 进入时间、离开时间、时长、费用、缴费状态（active/closed），
  出场即落「离开时间 + 费用 + paid=1」（离开默认已缴费）；
- `reservations`：一行一预约 —— 预约时间、保留截止、状态（active/consumed/expired）；
- `payments`：一行一次缴费 —— `type=spot` 现缴（当场付清，无余额）/
  `type=monthly` 月卡（valid_until 有效期内出场免计费）；缴费通道（POST /pay）写入；
- 进程重启：`State.restore_from_db()` 从本库恢复「在场票据 + 未过期预约 + 有效月卡 + 编号序列」，
  车已进场不会因重启查不到；
- `/debug/reset`：`db.reset()` 清库，恢复演示初始。

并发：每次操作独立连接、用完即关（与 server/app.py 的 _db 模式一致），
模块级锁串行化，避免句柄泄漏与写冲突。
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading
from pathlib import Path

# 默认落盘位置；`MOCK_DB` 环境变量可覆盖（测试脚本用独立库，避免与运行中的服务互相污染）
DB_PATH = Path(os.getenv("MOCK_DB") or
               Path(__file__).resolve().parents[1] / "data" / "mock.sqlite")
_FMT = "%Y-%m-%dT%H:%M:%S"


class MockDB:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # —— 连接与基础操作 ——
    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn = self._conn()
            try:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()

    def init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS tickets ("
                    "ticket_id TEXT PRIMARY KEY, plate TEXT NOT NULL, area TEXT NOT NULL,"
                    "vehicle_type TEXT NOT NULL, entry_time TEXT NOT NULL,"
                    "exit_time TEXT, duration_min INTEGER, fee REAL,"
                    "paid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',"
                    "paid_amount REAL NOT NULL DEFAULT 0)")
                # 旧库迁移：tickets 无 paid_amount 列时补列（出场前须结清）
                tcols = [r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()]
                if "paid_amount" not in tcols:
                    conn.execute("ALTER TABLE tickets ADD COLUMN paid_amount REAL NOT NULL DEFAULT 0")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS reservations ("
                    "reserve_id TEXT PRIMARY KEY, plate TEXT NOT NULL, area TEXT,"
                    "reserve_time TEXT NOT NULL, keep_until TEXT NOT NULL,"
                    "status TEXT NOT NULL DEFAULT 'active')")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS payments ("
                    "payment_id TEXT PRIMARY KEY, plate TEXT NOT NULL,"
                    "type TEXT NOT NULL DEFAULT 'spot',"
                    "amount REAL NOT NULL, pay_time TEXT NOT NULL, valid_until TEXT)")
                # 旧版预缴表（含 status，无 type）语义已废弃：重建为空表
                cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
                if "type" not in cols:
                    conn.execute("DROP TABLE payments")
                    conn.execute(
                        "CREATE TABLE payments ("
                        "payment_id TEXT PRIMARY KEY, plate TEXT NOT NULL,"
                        "type TEXT NOT NULL DEFAULT 'spot',"
                        "amount REAL NOT NULL, pay_time TEXT NOT NULL, valid_until TEXT)")
                conn.commit()
            finally:
                conn.close()

    def reset(self) -> None:
        """清空全部记录（/debug/reset 用；随后由 state.reset() 重建初始驻车）。"""
        self._run("DELETE FROM tickets")
        self._run("DELETE FROM reservations")
        self._run("DELETE FROM payments")

    # —— 票据（进出场 / 缴费）——
    def save_entry(self, ticket) -> None:
        """入场落库（进入时间；离开时间/费用/缴费状态留空，status=active）。"""
        self._run(
            "INSERT OR REPLACE INTO tickets (ticket_id, plate, area, vehicle_type,"
            " entry_time, exit_time, duration_min, fee, paid, status, paid_amount)"
            " VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 'active', 0)",
            (ticket.ticket_id, ticket.plate, ticket.area, ticket.vehicle_type,
             ticket.entry_time.strftime(_FMT)))

    def add_ticket_payment(self, ticket_id: str, amount: float) -> None:
        """累计票据已结清金额（现缴入账；出场拦截校验用）。"""
        self._run("UPDATE tickets SET paid_amount = paid_amount + ? WHERE ticket_id=?",
                  (float(amount), ticket_id))

    def mark_exited(self, ticket_id: str, exit_time: dt.datetime,
                    duration_min: int | None, fee: float | None,
                    paid: bool = True) -> None:
        """出场落库：离开时间 + 时长 + 费用 + 缴费状态（离开默认已缴费 paid=1）。"""
        self._run(
            "UPDATE tickets SET exit_time=?, duration_min=?, fee=?, paid=?, status='closed'"
            " WHERE ticket_id=?",
            (exit_time.strftime(_FMT), duration_min, fee, 1 if paid else 0, ticket_id))

    def load_active_tickets(self) -> list[dict]:
        return self._query("SELECT * FROM tickets WHERE status='active' ORDER BY ticket_id")

    def last_visit(self, plate: str) -> dict | None:
        """最近一次**正常结算**的出场记录（供 /vehicle 注入"上次停车：何时离开、费用、是否缴费"）。

        仅取 fee 非空的闭合记录——调试移除产生的记录（无结算数据）不参与。
        """
        rows = self._query(
            "SELECT * FROM tickets WHERE plate=? AND status='closed' AND fee IS NOT NULL"
            " ORDER BY exit_time DESC LIMIT 1", (plate,))
        if not rows:
            return None
        r = rows[0]
        r["paid"] = bool(r["paid"])
        return r

    def list_tickets(self, plate: str | None, limit: int) -> list[dict]:
        if plate:
            rows = self._query(
                "SELECT * FROM tickets WHERE plate=?"
                " ORDER BY COALESCE(exit_time, entry_time) DESC LIMIT ?", (plate, limit))
        else:
            rows = self._query(
                "SELECT * FROM tickets"
                " ORDER BY COALESCE(exit_time, entry_time) DESC LIMIT ?", (limit,))
        for r in rows:
            r["paid"] = bool(r["paid"])
        return rows

    def max_ticket_seq(self) -> int:
        rows = self._query("SELECT MAX(CAST(SUBSTR(ticket_id, 3) AS INTEGER)) m FROM tickets")
        return int(rows[0]["m"] or 0)

    def clear_ticket_paid(self, ticket_id: str) -> None:
        """调试移除等场景清零票据已结清金额。"""
        self._run("UPDATE tickets SET paid_amount = 0 WHERE ticket_id=?", (ticket_id,))

    # —— 预约 ——
    def save_reservation(self, res) -> None:
        self._run(
            "INSERT OR REPLACE INTO reservations (reserve_id, plate, area,"
            " reserve_time, keep_until, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (res.reserve_id, res.plate, res.area,
             res.reserve_time.strftime(_FMT), res.keep_until.strftime(_FMT)))

    def mark_reservation(self, reserve_id: str, status: str) -> None:
        self._run("UPDATE reservations SET status=? WHERE reserve_id=?", (status, reserve_id))

    def load_active_reservations(self) -> list[dict]:
        return self._query(
            "SELECT * FROM reservations WHERE status='active' ORDER BY reserve_id")

    def list_reservations(self, plate: str | None, limit: int) -> list[dict]:
        if plate:
            return self._query(
                "SELECT * FROM reservations WHERE plate=?"
                " ORDER BY reserve_time DESC LIMIT ?", (plate, limit))
        return self._query(
            "SELECT * FROM reservations ORDER BY reserve_time DESC LIMIT ?", (limit,))

    def max_reserve_seq(self) -> int:
        rows = self._query("SELECT MAX(CAST(SUBSTR(reserve_id, 3) AS INTEGER)) m FROM reservations")
        return int(rows[0]["m"] or 0)

    # —— 缴费：现缴 spot / 月卡 monthly ——
    def save_payment(self, payment_id: str, plate: str, ptype: str,
                     amount: float, pay_time: dt.datetime,
                     valid_until: dt.datetime | None = None) -> None:
        """缴费落库（spot=现缴；monthly=月卡，带有效期）。"""
        self._run(
            "INSERT OR REPLACE INTO payments (payment_id, plate, type,"
            " amount, pay_time, valid_until) VALUES (?, ?, ?, ?, ?, ?)",
            (payment_id, plate, ptype, float(amount), pay_time.strftime(_FMT),
             valid_until.strftime(_FMT) if valid_until else None))

    def active_monthly(self, plate: str, now: dt.datetime) -> dict | None:
        """该车牌当前有效的月卡（有效期未过）；无则 None。now 由调用方取虚拟时钟。"""
        rows = self._query(
            "SELECT * FROM payments WHERE plate=? AND type='monthly'"
            " AND valid_until IS NOT NULL AND valid_until > ?"
            " ORDER BY valid_until DESC LIMIT 1", (plate, now.strftime(_FMT)))
        return rows[0] if rows else None

    def load_active_monthly(self, now: dt.datetime) -> list[dict]:
        rows = self._query(
            "SELECT payment_id, plate, type AS ptype, amount, pay_time, valid_until"
            " FROM payments WHERE type='monthly' AND valid_until > ?"
            " ORDER BY valid_until", (now.strftime(_FMT),))
        return rows

    def max_payment_seq(self) -> int:
        rows = self._query("SELECT MAX(CAST(SUBSTR(payment_id, 3) AS INTEGER)) m FROM payments")
        return int(rows[0]["m"] or 0)

    def list_payments(self, plate: str | None, limit: int) -> list[dict]:
        if plate:
            return self._query(
                "SELECT * FROM payments WHERE plate=? ORDER BY pay_time DESC LIMIT ?",
                (plate, limit))
        return self._query(
            "SELECT * FROM payments ORDER BY pay_time DESC LIMIT ?", (limit,))


db = MockDB()
