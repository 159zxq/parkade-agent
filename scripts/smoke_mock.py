"""Mock 服务完整冒烟测试（成员B 任务卡 B7）——真实逻辑断言。

在 A 的 `scripts/smoke_stub.py` 基础上扩写：用 TestClient 直打全部路由，
断言关键字段与状态变化，末尾打印 `ALL PASS（共 N 项）`。

运行（项目根目录）：python scripts/smoke_mock.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from mock_service.main import app  # noqa: E402

client = TestClient(app)
_fails: list[str] = []
_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _count
    _count += 1
    print(f"{'[PASS]' if cond else '[FAIL]'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def main() -> int:
    # 0) 复位
    r = client.post("/debug/reset")
    check("TC-Mock-00 POST /debug/reset 200", r.status_code == 200 and r.json().get("ok") is True)

    # 1) B1 车位状态初始值（契约 §5）
    r = client.get("/lots/status")
    body = r.json()
    areas = {a["area"]: a for a in body.get("areas", [])}
    check("TC-Mock-01 /lots/status 返回 A/B/C",
          [a["area"] for a in body["areas"]] == ["A", "B", "C"])
    check("TC-Mock-01b 初始 A.free=27", areas["A"]["free"] == 27, str(areas["A"]))
    check("TC-Mock-01c 初始 B.free=52", areas["B"]["free"] == 52, str(areas["B"]))
    check("TC-Mock-01d 初始 C.free=58", areas["C"]["free"] == 58, str(areas["C"]))
    r = client.get("/lots/status", params={"area": "a"})
    check("TC-Mock-01e ?area=A 过滤生效", [a["area"] for a in r.json()["areas"]] == ["A"])
    r = client.get("/lots/status", params={"area": "Z"})
    check("TC-Mock-01f 未知区域 400 INVALID_PARAM",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM")

    # 2) B2 入场
    r = client.post("/entry", json={"plate": "京Z99999"})
    body = r.json()
    check("TC-Mock-02 /entry 正常入场 ok",
          r.status_code == 200 and body["ok"] and body["area"] == "A", str(body))
    check("TC-Mock-02b 票据号 T-0001", body["ticket_id"] == "T-0001", body.get("ticket_id"))
    check("TC-Mock-02c 入场后 A.free=26",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 26)

    # 3) 重复入场
    r = client.post("/entry", json={"plate": "京Z99999"})
    check("TC-Mock-03 重复车牌 400 ALREADY_INSIDE",
          r.status_code == 400 and r.json()["error"]["code"] == "ALREADY_INSIDE", str(r.json()))

    # 4) 虚时 set + 入场使用虚拟时间
    r = client.post("/debug/time", json={"set": "2026-11-10T10:00:00"})
    check("TC-Mock-04 set 虚拟时间生效",
          r.json()["virtual_time"] == "2026-11-10T10:00:00", str(r.json()))
    r = client.post("/entry", json={"plate": "京A12345"})
    check("TC-Mock-05 入场 entry_time 取虚拟时间",
          r.json()["entry_time"] == "2026-11-10T10:00:00", str(r.json()))

    # 5) 时间 +120
    r = client.post("/debug/time", json={"offset_minutes": 120})
    check("TC-Mock-06 时间偏移 +120 生效",
          r.json()["virtual_time"] == "2026-11-10T12:00:00", str(r.json()))

    # 6) B3 dry_run 报价（契约 §4 样例1 = 12.00），且状态不变
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    body = r.json()
    check("TC-Mock-07 dry_run 报价 total=12.00", body["total"] == 12.0, str(body))
    check("TC-Mock-07b 明细正确（105 分/4 单位）",
          body["duration_min"] == 120 and body["billable_minutes"] == 105
          and body["units"] == 4, str(body))
    check("TC-Mock-07c dry_run 不改状态（车还在）",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 25)

    # 7) 真出场 → 12.00，A.free 回补到 26
    r = client.post("/exit", json={"plate": "京A12345"})
    check("TC-Mock-08 真出场 total=12.00", r.json()["total"] == 12.0, str(r.json()))
    check("TC-Mock-08b 出场后 A.free 回补到 26",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 26)

    # 8) 未在场出场
    r = client.post("/exit", json={"plate": "京A12345"})
    check("TC-Mock-09 未在场 400 NOT_INSIDE",
          r.status_code == 400 and r.json()["error"]["code"] == "NOT_INSIDE", str(r.json()))

    # 9) B6 预约 + keep_until
    r = client.post("/reserve",
                    json={"plate": "京B88888", "reserve_time": "2026-11-10T18:00:00", "area": "B"})
    body = r.json()
    check("TC-Mock-10 /reserve keep_until=18:30",
          r.status_code == 200 and body["keep_until"] == "2026-11-10T18:30:00", str(body))
    check("TC-Mock-10b reserve_id 递进", body["reserve_id"] == "R-0001", body.get("reserve_id"))
    r = client.post("/reserve", json={"plate": "京B88888", "reserve_time": "2026-11-10T19:00:00"})
    check("TC-Mock-11 重复预约 400 RESERVE_CONFLICT",
          r.status_code == 400 and r.json()["error"]["code"] == "RESERVE_CONFLICT", str(r.json()))

    # 10) 契约 §4 例2（跨入夜间）：21:30→23:10 应收 7.50
    client.post("/debug/reset")
    client.post("/debug/time", json={"set": "2026-11-10T21:30:00"})
    client.post("/entry", json={"plate": "京A12345"})
    client.post("/debug/time", json={"offset_minutes": 100})
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    check("TC-Mock-20 例2 跨夜间 total=7.50", r.json()["total"] == 7.5, str(r.json()))

    # 11) B5 相机图三变体 + truth
    for v in ("normal", "blur", "occluded"):
        r = client.get("/camera/cam-1/capture", params={"variant": v})
        check(f"TC-Mock-13 capture {v} 返回 JPEG",
              r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
              and len(r.content) > 1000, f"status={r.status_code} len={len(r.content)}")
    r = client.get("/debug/truth/cam-1")
    body = r.json()
    check("TC-Mock-12 /debug/truth/cam-1 车牌对账",
          body["plate"] == "京A12345" and body["vehicle_type"] == "小型车", str(body))
    r = client.get("/camera/cam-9/capture")
    check("TC-Mock-14 未知相机 404 CAMERA_NOT_FOUND",
          r.status_code == 404 and r.json()["error"]["code"] == "CAMERA_NOT_FOUND", str(r.json()))
    r = client.get("/camera/cam-1/capture", params={"variant": "xxx"})
    check("TC-Mock-14b 非法 variant 400 INVALID_PARAM",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM", str(r.json()))

    # 12) B4 时钟
    r = client.post("/debug/time/reset")
    check("TC-Mock-15 时钟 reset offset=0", r.json()["offset_minutes"] == 0, str(r.json()))
    client.post("/debug/time", json={"offset_minutes": 90})
    check("TC-Mock-16 GET /debug/time offset=90",
          client.get("/debug/time").json()["offset_minutes"] == 90)

    # 13) 全量 reset 恢复初始
    client.post("/debug/reset")
    check("TC-Mock-17 reset 后恢复初始 A.free=27",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 27)

    # 14) 单车查询 /vehicle（契约 v1.2）
    r = client.get("/vehicle/京A12345")
    body = r.json()
    check("TC-Mock-30 /vehicle 不在场 inside=false",
          r.status_code == 200 and body["inside"] is False and body["plate"] == "京A12345", str(body))
    client.post("/debug/time", json={"set": "2026-11-10T10:00:00"})
    client.post("/entry", json={"plate": "京A12345", "camera_id": "cam-1"})
    client.post("/debug/time", json={"offset_minutes": 90})
    r = client.get("/vehicle/京A12345")
    body = r.json()
    check("TC-Mock-31 /vehicle 在场含时长（90 分钟）",
          body["inside"] is True and body["duration_min"] == 90
          and body["entry_time"] == "2026-11-10T10:00:00" and body["area"] == "A", str(body))
    r = client.get("/vehicle/abc")
    check("TC-Mock-32 /vehicle 任意车牌不报错（不在场）",
          r.status_code == 200 and r.json()["inside"] is False, str(r.json()))
    client.post("/debug/reset")

    # 15) 看板调试 /debug/lots（契约 v1.1）
    r = client.post("/debug/lots", json={"area": "A", "total": 50})
    check("TC-Mock-33 /debug/lots 改总车位", r.json()["total"] == 50, str(r.json()))
    r = client.post("/debug/lots", json={"area": "A", "occupied": 45})
    check("TC-Mock-34 /debug/lots 改占用", r.json()["occupied"] == 45 and r.json()["free"] == 5, str(r.json()))
    r = client.post("/debug/lots", json={"area": "A", "occupied": 99})
    check("TC-Mock-35 超容量 400 INVALID_PARAM",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM", str(r.json()))
    client.post("/debug/reset")
    check("TC-Mock-36 reset 恢复容量与占用",
          client.get("/lots/status?area=A").json()["areas"][0]["total"] == 30)

    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} 项失败: {_fails}'}（共 {_count} 项）")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
