"""Mock 服务完整冒烟测试 —— 真实逻辑断言。

用 TestClient 直打全部路由，断言关键字段与状态变化，末尾打印 `ALL PASS（共 N 项）`。

运行（项目根目录）：python tests/smoke_mock.py
测试隔离：默认写独立库 data/mock_smoke.sqlite（MOCK_DB 可覆盖），
不影响运行中服务的 data/mock.sqlite。
相机用例依赖 data/captures/ 测试图（缺失时由 make_captures 自动补齐）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# 独立记录库：必须在 import mock_service 之前设置（db 模块导入时读取）
os.environ.setdefault("MOCK_DB", str(ROOT / "data" / "mock_smoke.sqlite"))

from fastapi.testclient import TestClient  # noqa: E402

from make_captures import ensure_captures  # noqa: E402
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
    ensure_captures()   # 相机用例的测试图（缺失自动补齐）
    # 0) 复位
    r = client.post("/debug/reset")
    check("TC-Mock-00 POST /debug/reset 200", r.status_code == 200 and r.json().get("ok") is True)

    # 1) 车位状态初始值
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

    # 2) 入场
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

    # 6) dry_run 报价（样例1 = 12.00），且状态不变
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    body = r.json()
    check("TC-Mock-07 dry_run 报价 total=12.00", body["total"] == 12.0, str(body))
    check("TC-Mock-07b 明细正确（105 分/4 单位）",
          body["duration_min"] == 120 and body["billable_minutes"] == 105
          and body["units"] == 4, str(body))
    check("TC-Mock-07c dry_run 不改状态（车还在）",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 25)

    # 7) 出场拦截：未结清 → 400 FEE_UNPAID；缴费结清后出场
    r = client.post("/exit", json={"plate": "京A12345"})
    check("TC-Mock-08 未结清出场 400 FEE_UNPAID",
          r.status_code == 400 and r.json()["error"]["code"] == "FEE_UNPAID", str(r.json()))
    r = client.post("/pay", json={"plate": "京A12345"})
    check("TC-Mock-08b 现缴结清 12.00（P-0001）",
          r.json()["amount"] == 12.0, str(r.json()))
    r = client.post("/exit", json={"plate": "京A12345"})
    check("TC-Mock-08c 结清后出场 total=12.00", r.json()["total"] == 12.0, str(r.json()))
    check("TC-Mock-08d 出场后 A.free 回补到 26",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 26)

    # 8) 未在场出场
    r = client.post("/exit", json={"plate": "京A12345"})
    check("TC-Mock-09 未在场 400 NOT_INSIDE",
          r.status_code == 400 and r.json()["error"]["code"] == "NOT_INSIDE", str(r.json()))

    # 9) 预约 + keep_until
    r = client.post("/reserve",
                    json={"plate": "京B88888", "reserve_time": "2026-11-10T18:00:00", "area": "B"})
    body = r.json()
    check("TC-Mock-10 /reserve keep_until=18:30",
          r.status_code == 200 and body["keep_until"] == "2026-11-10T18:30:00", str(body))
    check("TC-Mock-10b reserve_id 递进", body["reserve_id"] == "R-0001", body.get("reserve_id"))
    r = client.post("/reserve", json={"plate": "京B88888", "reserve_time": "2026-11-10T19:00:00"})
    check("TC-Mock-11 重复预约 400 RESERVE_CONFLICT",
          r.status_code == 400 and r.json()["error"]["code"] == "RESERVE_CONFLICT", str(r.json()))

    # 10) 例2（跨入夜间）：21:30→23:10 应收 7.50
    client.post("/debug/reset")
    client.post("/debug/time", json={"set": "2026-11-10T21:30:00"})
    client.post("/entry", json={"plate": "京A12345"})
    client.post("/debug/time", json={"offset_minutes": 100})
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    check("TC-Mock-20 例2 跨夜间 total=7.50", r.json()["total"] == 7.5, str(r.json()))

    # 11) 相机测试图三变体 + truth
    for v in ("normal", "blur", "occluded"):
        r = client.get("/camera/cam-1/capture", params={"variant": v})
        check(f"TC-Mock-13 /camera 下发测试图 {v} 返回 JPEG",
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

    # 12) 时钟
    r = client.post("/debug/time/reset")
    check("TC-Mock-15 时钟 reset offset=0", r.json()["offset_minutes"] == 0, str(r.json()))
    client.post("/debug/time", json={"offset_minutes": 90})
    check("TC-Mock-16 GET /debug/time offset=90",
          client.get("/debug/time").json()["offset_minutes"] == 90)

    # 13) 全量 reset 恢复初始
    client.post("/debug/reset")
    check("TC-Mock-17 reset 后恢复初始 A.free=27",
          client.get("/lots/status?area=A").json()["areas"][0]["free"] == 27)

    # 14) 单车查询 /vehicle
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

    # 15) 看板调试 /debug/lots
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

    # 16) 记录库 + 预约占位
    client.post("/debug/reset")
    a0 = client.get("/lots/status?area=A").json()["areas"][0]
    check("TC-Mock-40 看板含 reserved 字段（初始 0）",
          a0.get("reserved") == 0, str(a0))
    r = client.post("/reserve", json={"plate": "京B88888",
                                      "reserve_time": "2026-11-10T18:00:00", "area": "B"})
    b1 = client.get("/lots/status?area=B").json()["areas"][0]
    check("TC-Mock-41 预约后在 B 区占位（reserved=1、free−1）",
          r.json()["ok"] and b1["reserved"] == 1 and b1["free"] == 51, str(b1))
    r = client.post("/entry", json={"plate": "京B88888"})
    body = r.json()
    b2 = client.get("/lots/status?area=B").json()["areas"][0]
    check("TC-Mock-42 预约车入场默认进预约区且核销预约",
          body["area"] == "B" and body["reservation_consumed"] is True, str(body))
    check("TC-Mock-42b 核销后 reserved=0、占用 +1（free 不变）",
          b2["reserved"] == 0 and b2["occupied"] == 9 and b2["free"] == 51, str(b2))

    r = client.get("/records?plate=京B88888")
    body = r.json()
    t = body["tickets"][0]
    check("TC-Mock-43 /records 票据含进入时间（离开为空=在场）",
          r.status_code == 200 and t["plate"] == "京B88888" and t["entry_time"]
          and t["exit_time"] is None and t["status"] == "active", str(t))
    check("TC-Mock-43b /records 预约已核销（status=consumed）",
          body["reservations"][0]["status"] == "consumed", str(body["reservations"][0]))

    client.post("/debug/time", json={"offset_minutes": 60})
    r = client.post("/exit", json={"plate": "京B88888"})
    check("TC-Mock-44 未结清出场被拦截 400 FEE_UNPAID（先缴清后出场）",
          r.status_code == 400 and r.json()["error"]["code"] == "FEE_UNPAID", str(r.json()))
    r = client.post("/pay", json={"plate": "京B88888"})   # 现缴结清
    check("TC-Mock-44a 现缴结清待缴费用", r.json()["ok"] is True, str(r.json()))
    r = client.post("/exit", json={"plate": "京B88888"})
    check("TC-Mock-44b 结清后出场 paid=true", r.json()["paid"] is True, str(r.json()))
    t = client.get("/records?plate=京B88888").json()["tickets"][0]
    check("TC-Mock-44c 出场落库：离开时间/费用/已缴费/closed",
          t["exit_time"] and t["fee"] == r.json()["total"] and t["paid"] is True
          and t["status"] == "closed", str(t))

    v = client.get("/vehicle/京B88888").json()
    check("TC-Mock-45 /vehicle 含 last_visit（离开时间/费用/缴费）",
          v["inside"] is False and v["last_visit"] and v["last_visit"]["paid"] is True
          and v["last_visit"]["exit_time"], str(v))

    # 17) 重启恢复：同一数据库文件重建 State（模拟进程重启）
    import mock_service.state as _st
    from mock_service.db import MockDB, db as _db

    client.post("/debug/reset")
    client.post("/entry", json={"plate": "京A12345", "camera_id": "cam-1"})
    t_before = client.get("/records?plate=京A12345").json()["tickets"][0]
    _st.state.reset()
    _st.state.restore_from_db()
    v = client.get("/vehicle/京A12345").json()
    check("TC-Mock-46 模拟重启后在场车自动恢复（不再'说成没进场'）",
          v["inside"] is True and v["area"] == "A"
          and v["ticket_id"] == t_before["ticket_id"], str(v))
    check("TC-Mock-46b 恢复后看板占用一致（A occupied=4）",
          client.get("/lots/status?area=A").json()["areas"][0]["occupied"] == 4)

    # 18) reset 清空记录库
    client.post("/debug/reset")
    check("TC-Mock-47 reset 清空记录库",
          client.get("/records").json()["tickets"] == []
          and client.get("/records").json()["reservations"] == [])

    # 19) 缴费：现缴 spot / 月卡 monthly
    client.post("/debug/reset")
    r = client.post("/pay", json={"plate": "京A12345"})
    check("TC-Mock-50 不在场现缴 400 NOT_INSIDE（无待缴费用）",
          r.status_code == 400 and r.json()["error"]["code"] == "NOT_INSIDE", str(r.json()))
    r = client.post("/pay", json={"plate": "京A12345", "type": "xxx"})
    check("TC-Mock-51 非法 type 400 INVALID_PARAM",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM", str(r.json()))

    # 在场现缴：按当前出场费当场结清
    client.post("/debug/time", json={"set": "2026-11-10T10:00:00"})
    client.post("/entry", json={"plate": "京A12345"})
    client.post("/debug/time", json={"offset_minutes": 120})
    r = client.post("/pay", json={"plate": "京A12345"})
    body = r.json()
    check("TC-Mock-52 在场现缴=当前出场费 12.00（P-0001，type=spot）",
          r.status_code == 200 and body["type"] == "spot" and body["amount"] == 12.0
          and body["payment_id"] == "P-0001" and body["duration_min"] == 120, str(body))
    pays = client.get("/records?plate=京A12345").json()["payments"]
    check("TC-Mock-52b /records 缴费记录 type=spot、无预缴字段",
          len(pays) == 1 and pays[0]["type"] == "spot" and "status" not in pays[0], str(pays))

    # 月卡：统一价 300（不分车型），有效期内出场免计费
    r = client.post("/pay", json={"plate": "京B88888", "type": "monthly"})
    body = r.json()
    check("TC-Mock-53 月卡办理（统一价 300.00，不分车型，valid_until 30 天后）",
          r.status_code == 200 and body["type"] == "monthly" and body["amount"] == 300.0
          and "vehicle_type" not in body and body["valid_until"], str(body))
    r = client.post("/pay", json={"plate": "京B88888", "type": "monthly"})
    check("TC-Mock-53b 重复办月卡 400 INVALID_PARAM",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAM", str(r.json()))
    v = client.get("/vehicle/京B88888").json()
    check("TC-Mock-53c /vehicle 显示月卡生效中",
          v["monthly"] and v["monthly"]["valid_until"], str(v))
    client.post("/entry", json={"plate": "京B88888"})
    client.post("/debug/time", json={"offset_minutes": 120})
    r = client.post("/exit", json={"plate": "京B88888"})
    body = r.json()
    check("TC-Mock-54 月卡车出场免计费（total=0，monthly=true）",
          body["total"] == 0.0 and body["monthly"] is True and "prepaid_used" not in body, str(body))

    # 重启恢复：有效月卡保留
    _st.state.reset()
    _st.state.restore_from_db()
    v = client.get("/vehicle/京B88888").json()
    check("TC-Mock-55 模拟重启后月卡恢复（仍生效中）",
          v["monthly"] is not None, str(v))
    check("TC-Mock-55b 恢复仅含有效月卡（现缴无预缴语义，不参与恢复）",
          all(p.ptype == "monthly" for p in _st.state.payments.values())
          and len(_st.state.payments) == 1, str(_st.state.payments))

    client.post("/debug/reset")
    check("TC-Mock-56 reset 清空缴费记录",
          client.get("/records").json()["payments"] == [])

    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} 项失败: {_fails}'}（共 {_count} 项）")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
