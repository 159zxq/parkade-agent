"""stub 冒烟测试（A0 验收）——不启动服务，用 TestClient 直打全部路由。

运行：python scripts/smoke_stub.py
B 任务卡 B7 会在此基础上扩写成完整的 scripts/smoke_mock.py（真实逻辑断言）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from mock_service.main import app  # noqa: E402

client = TestClient(app)
_fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'[PASS]' if cond else '[FAIL]'} {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def main() -> int:
    # 1) 车位状态：3 个区，A 区 free = 30-3
    r = client.get("/lots/status")
    body = r.json()
    check("GET /lots/status 200 且 ok",
          r.status_code == 200 and body.get("ok") is True, str(r.status_code))
    areas = body.get("areas", [])
    check("返回 A/B/C 三个区", [a["area"] for a in areas] == ["A", "B", "C"])
    a = next((x for x in areas if x["area"] == "A"), {})
    check("A 区 free=27（30-3 初始驻车）", a.get("free") == 27, str(a))

    # 2) 入场：stub 返回 ok + 票据
    r = client.post("/entry", json={"plate": "京A12345", "camera_id": "cam-1"})
    body = r.json()
    check("POST /entry 200 且返回 ticket_id",
          r.status_code == 200 and body.get("ok") is True and body.get("ticket_id"), str(body))

    # 3) 出场：stub 走真实 calc_fee（2 小时小型车 = 12.00 元）
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    body = r.json()
    check("POST /exit total=12.00（接通唯一计费实现）",
          r.status_code == 200 and body.get("total") == 12.0, str(body))

    # 4) 相机：已知相机返回图片字节；未知相机 404 且错误码正确
    r = client.get("/camera/cam-1/capture")
    check("GET /camera/cam-1/capture 返回图片",
          r.status_code == 200 and r.headers["content-type"].startswith("image/")
          and len(r.content) > 100, f"len={len(r.content)}")
    r = client.get("/camera/cam-9/capture")
    check("未知相机 404 CAMERA_NOT_FOUND",
          r.status_code == 404 and r.json().get("error", {}).get("code") == "CAMERA_NOT_FOUND",
          str(r.json()))

    # 5) truth 对账：cam-2 车牌固定
    r = client.get("/debug/truth/cam-2")
    check("GET /debug/truth/cam-2 车牌=京B88888",
          r.status_code == 200 and r.json().get("plate") == "京B88888", str(r.json()))

    # 6) 虚拟时钟：注入 +120 后 offset=120，reset 后归零
    client.post("/debug/time", json={"offset_minutes": 120})
    body = client.get("/debug/time").json()
    check("时钟注入 +120 生效", body.get("offset_minutes") == 120, str(body))
    client.post("/debug/time/reset")
    body = client.get("/debug/time").json()
    check("时钟 reset 归零", body.get("offset_minutes") == 0, str(body))

    # 7) 预约：keep_until = reserve_time + 30min
    r = client.post("/reserve", json={"plate": "京B88888", "reserve_time": "2026-11-10T18:00:00"})
    body = r.json()
    check("POST /reserve keep_until=18:30",
          r.status_code == 200 and body.get("keep_until") == "2026-11-10T18:30:00", str(body))

    # 8) reset 全量：200 ok
    r = client.post("/debug/reset")
    check("POST /debug/reset 200", r.status_code == 200 and r.json().get("ok") is True)

    total = 12
    if _fails:
        print(f"\n{len(_fails)} 项失败: {_fails}")
        return 1
    print(f"\nALL PASS（共 {total} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
