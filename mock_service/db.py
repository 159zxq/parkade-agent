"""Mock 服务落盘记录库（SQLite）—— 进出场 / 缴费 / 预约全留痕（契约 v1.3）。

- 位置：`data/mock.sqlite`（gitignored；与 checkpoints/web.sqlite 同级）；
- `tickets`：一行一票据 —— 进入时间、离开时间、时长、费用、缴费状态（active/closed），
  出场即落「离开时间 + 费用 + paid=1」（离开默认已缴费）；
- `reservations`：一行一预约 —— 预约时间、保留截止、状态（active/consumed/expired）；
- 进程重启：`State.restore_from_db()` 从本库恢复「在场票据 + 未过期预约 + 编号序列」，
  根治"车已进场、重启后却查不到（被说成没进场）"；
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
                    "paid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active')")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS reservations ("
                    "reserve_id TEXT PRIMARY KEY, plate TEXT NOT NULL, area TEXT,"
                    "reserve_time TEXT NOT NULL, keep_until TEXT NOT NULL,"
                    "status TEXT NOT NULL DEFAULT 'active')")
                conn.commit()
            finally:
                conn.close()

    def reset(self) -> None:
        """清空全部记录（/debug/reset 用；随后由 state.reset() 重建初始驻车）。"""
        self._run("DELETE FROM tickets")
        self._run("DELETE FROM reservations")

    # —— 票据（进出场 / 缴费）——
    def save_entry(self, ticket) -> None:
        """入场落库（进入时间；离开时间/费用/缴费状态留空，status=active）。"""
        self._run(
            "INSERT OR REPLACE INTO tickets (ticket_id, plate, area, vehicle_type,"
            " entry_time, exit_time, duration_min, fee, paid, status)"
            " VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 'active')",
            (ticket.ticket_id, ticket.plate, ticket.area, ticket.vehicle_type,
             ticket.entry_time.strftime(_FMT)))

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


db = MockDB()
