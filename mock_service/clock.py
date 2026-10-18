"""虚拟时钟。

全仓库业务时间**必须**取自 `clock.now()`；本文件是唯一允许出现
`datetime.now()` 的地方（自身实现用）。自查命令：
    grep -rn "datetime.now()" mock_service/ | grep -v clock.py   # 应为空
"""
from __future__ import annotations

from datetime import datetime, timedelta


class VirtualClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        """业务时间（虚拟时间）。所有票据时间、计费时间用它。"""
        return datetime.now() + self._offset

    def real_now(self) -> datetime:
        """真实时间，仅供 /debug/time 展示对比。"""
        return datetime.now()

    def add_offset(self, minutes: int) -> None:
        self._offset += timedelta(minutes=minutes)

    def set_time(self, iso: str) -> None:
        self._offset = datetime.fromisoformat(iso) - datetime.now()

    def offset_minutes(self) -> float:
        return self._offset.total_seconds() / 60

    def reset(self) -> None:
        self._offset = timedelta(0)


clock = VirtualClock()
