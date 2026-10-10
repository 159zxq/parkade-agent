"""虚拟时钟（成员B 任务卡 B4）。

铁律：mock_service 内所有时间必须取自 clock.now()，除本文件外禁止 datetime.now()。
自查命令：grep -rn "datetime.now()" mock_service/  —— 除本文件外不许有结果。
"""

from __future__ import annotations

from datetime import datetime, timedelta


class VirtualClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now() + self._offset

    def add_offset(self, minutes: int) -> None:
        self._offset += timedelta(minutes=minutes)

    def set_time(self, iso: str) -> None:
        self._offset = datetime.fromisoformat(iso) - datetime.now()

    def reset(self) -> None:
        self._offset = timedelta(0)

    @property
    def offset_minutes(self) -> int:
        # 契约 §3.7 返回整数分钟
        return int(round(self._offset.total_seconds() / 60))


clock = VirtualClock()
