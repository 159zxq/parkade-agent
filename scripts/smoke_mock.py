"""Mock 服务冒烟脚本（成员B 任务卡 B7）。

把《成员B执行方案》里的全部验收 curl 改写成 httpx 断言脚本，
逐条断言关键字段，末尾打印 ALL PASS (N cases)。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/smoke_mock.py
若 8000 端口没有服务，脚本会自动拉起 uvicorn 子进程，跑完自动关闭。

可用环境变量 MOCK_BASE 覆盖地址（默认 http://127.0.0.1:8000）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("MOCK_BASE", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent.parent

_passed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _passed.append(name)
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        raise AssertionError(f"{name} 断言失败: {detail}")


def wait_server(client: httpx.Client, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = client.get("/", timeout=1.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.4)
    return False


def run_cases(client: httpx.Client) -> None:
    # 0) 复位
    r = client.post("/debug/reset")
    check("TC-Mock-00 reset 复位", r.status_code == 200 and r.json().get("ok") is True)

    # 1) 车位状态初始值（契约 §5）
    r = client.get("/lots/status")
    areas = {a["area"]: a for a in r.json()["areas"]}
    check("TC-Mock-01 /lots/status 初始 A.free=27", areas["A"]["free"] == 27, str(areas["A"]))
    check("TC-Mock-01b 初始 B.free=52", areas["B"]["free"] == 52, str(areas["B"]))
    check("TC-Mock-01c 初始 C.free=58", areas["C"]["free"] == 58, str(areas["C"]))

    # 2) 入场 → 票据递增、余位 -1
    r = client.post("/entry", json={"plate": "京Z99999"})
    j = r.json()
    check("TC-Mock-02 /entry 正常入场 ok", r.status_code == 200 and j["ok"] and j["area"] == "A", str(j))
    check("TC-Mock-02b 票据号 T-0001", j["ticket_id"] == "T-0001", j["ticket_id"])
    r = client.get("/lots/status?area=A")
    check("TC-Mock-02c 入场后 A.free=26", r.json()["areas"][0]["free"] == 26)

    # 3) 重复入场 → 400 ALREADY_INSIDE
    r = client.post("/entry", json={"plate": "京Z99999"})
    j = r.json()
    check("TC-Mock-03 重复车牌 400 ALREADY_INSIDE",
          r.status_code == 400 and j["error"]["code"] == "ALREADY_INSIDE", str(j))

    # 4) 虚拟时钟 set
    r = client.post("/debug/time", json={"set": "2026-11-10T10:00:00"})
    check("TC-Mock-04 set 虚拟时间", r.json()["virtual_time"] == "2026-11-10T10:00:00", str(r.json()))

    # 5) 入场（相机车牌），entry_time 应为虚拟时间
    r = client.post("/entry", json={"plate": "京A12345"})
    j = r.json()
    check("TC-Mock-05 入场 entry_time 取虚拟时间", j["entry_time"] == "2026-11-10T10:00:00", str(j))

    # 6) 时间 +120 分钟
    r = client.post("/debug/time", json={"offset_minutes": 120})
    check("TC-Mock-06 时间偏移 +120 生效", r.json()["virtual_time"] == "2026-11-10T12:00:00", str(r.json()))

    # 7) dry_run 报价 → total=12.00，且状态不变
    r = client.post("/exit", json={"plate": "京A12345", "dry_run": True})
    j = r.json()
    check("TC-Mock-07 dry_run 报价 total=12.00", abs(j["total"] - 12.0) < 1e-6, str(j))
    check("TC-Mock-07b dry_run 明细正确 (105 分/4 单位)",
          j["duration_min"] == 120 and j["billable_minutes"] == 105 and j["units"] == 4, str(j))
    r = client.get("/lots/status?area=A")
    check("TC-Mock-07c dry_run 不改状态（车还在）", r.json()["areas"][0]["free"] == 25)

    # 8) 真出场 → total=12.00，余位回补
    r = client.post("/exit", json={"plate": "京A12345"})
    j = r.json()
    check("TC-Mock-08 真出场 total=12.00", abs(j["total"] - 12.0) < 1e-6, str(j))
    r = client.get("/lots/status?area=A")
    check("TC-Mock-08b 出场后 A.free 回补到 26", r.json()["areas"][0]["free"] == 26)

    # 9) 不在场出场 → 400 NOT_INSIDE
    r = client.post("/exit", json={"plate": "京A12345"})
    j = r.json()
    check("TC-Mock-09 未在场 400 NOT_INSIDE",
          r.status_code == 400 and j["error"]["code"] == "NOT_INSIDE", str(j))

    # 10) 预约 → keep_until = reserve_time + 30min
    r = client.post("/reserve", json={"plate": "京B88888", "reserve_time": "2026-11-10T18:00:00", "area": "B"})
    j = r.json()
    check("TC-Mock-10 /reserve ok 且 keep_until 正确",
          r.status_code == 200 and j["keep_until"] == "2026-11-10T18:30:00", str(j))
    check("TC-Mock-10b reserve_id 递进", j["reserve_id"] == "R-0001", j["reserve_id"])

    # 11) 重复预约 → 400 RESERVE_CONFLICT
    r = client.post("/reserve", json={"plate": "京B88888", "reserve_time": "2026-11-10T19:00:00"})
    j = r.json()
    check("TC-Mock-11 重复预约 400 RESERVE_CONFLICT",
          r.status_code == 400 and j["error"]["code"] == "RESERVE_CONFLICT", str(j))

    # 12) 真值对账
    r = client.get("/debug/truth/cam-1")
    j = r.json()
    check("TC-Mock-12 /debug/truth/cam-1 车牌对账",
          j["plate"] == "京A12345" and j["vehicle_type"] == "小型车", str(j))

    # 13) 相机图三变体均返回 JPEG
    for v in ("normal", "blur", "occluded"):
        r = client.get(f"/camera/cam-1/capture?variant={v}")
        check(f"TC-Mock-13 capture {v} 返回 JPEG",
              r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 1000,
              f"status={r.status_code} type={r.headers.get('content-type')} len={len(r.content)}")

    # 14) 未知相机 → 404 CAMERA_NOT_FOUND
    r = client.get("/camera/cam-9/capture")
    j = r.json()
    check("TC-Mock-14 未知相机 404 CAMERA_NOT_FOUND",
          r.status_code == 404 and j["error"]["code"] == "CAMERA_NOT_FOUND", str(j))

    # 15) 时钟 reset
    r = client.post("/debug/time/reset")
    check("TC-Mock-15 时钟 reset offset=0", r.json()["offset_minutes"] == 0, str(r.json()))

    # 16) 时间注入 +90 后 GET /debug/time 返回 offset_minutes=90
    client.post("/debug/time", json={"offset_minutes": 90})
    r = client.get("/debug/time")
    check("TC-Mock-16 GET /debug/time offset_minutes=90", r.json()["offset_minutes"] == 90, str(r.json()))

    # 17) 全状态 reset 后恢复初始
    client.post("/debug/reset")
    r = client.get("/lots/status")
    areas = {a["area"]: a for a in r.json()["areas"]}
    check("TC-Mock-17 reset 后恢复初始 A.free=27", areas["A"]["free"] == 27, str(areas["A"]))


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=10.0)
    proc = None
    try:
        if not wait_server(client, timeout=3.0):
            print("[info] 未发现运行中的 mock 服务，自动拉起 uvicorn ...")
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mock_service.main:app",
                 "--port", "8000", "--log-level", "warning"],
                cwd=str(ROOT),
            )
            if not wait_server(client, timeout=25.0):
                print("[FAIL] 无法连接 mock 服务")
                return 1

        run_cases(client)
        print(f"\nALL PASS ({len(_passed)} cases)")
        return 0
    except AssertionError as e:
        print(f"\nSMOKE FAILED: {e}")
        return 1
    finally:
        client.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
