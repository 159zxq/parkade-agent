"""Mock 服务共享常量与状态（stub 版）。

B 的任务卡 B1–B6：在本模块基础上引入可变状态（占用车位副本、票据表、预约表）
并实现 /debug/reset 的状态恢复；常量表（相机映射、初始占用）不要改，
要与 docs/接口契约.md §2/§5 保持一致。
"""
from __future__ import annotations

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
