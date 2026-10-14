"""一键重置全部数据到初始状态（演示前 / 答辩前用）。

重置内容（四项）：
1. **Mock 服务**（data/mock.sqlite + 内存车位/票据/记录 + 虚拟时钟）：
   服务在线时走 `POST /debug/reset`（内存与落盘一并复位）；未在线则直接清空库文件
   （下次启动即初始状态）；
2. **Web 用户与对话历史**（data/web.sqlite）：清空 users/messages，重建初始
   「演示用户（京A12345）」，用户编号从 1 重新开始；
3. **Agent 多轮记忆**（data/checkpoints.sqlite）：清空全部 thread 会话记忆；
4. **运行期图片**（data/captures、data/uploads）：抓拍图与上传图（`--keep-images` 可保留）。

用法（项目根目录，两个服务可开可关均适用）：
    python tests/reset_all.py            # 交互确认
    python tests/reset_all.py --yes      # 跳过确认（脚本/自动化）
    python tests/reset_all.py --yes --keep-images   # 保留图片

重置后**无需重启服务**，刷新浏览器页面即可；种子数据（车位表、相机表、费率、
知识库、docs/样例 图）不受影响。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from common.config import MOCK_BASE  # noqa: E402

DATA = ROOT / "data"
MOCK_DB = DATA / "mock.sqlite"
WEB_DB = DATA / "web.sqlite"
CKPT_DB = DATA / "checkpoints.sqlite"
IMG_DIRS = (DATA / "captures", DATA / "uploads")

INITIAL_USER = ("演示用户", "京A12345")


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone()
    return row is not None


def reset_mock() -> str:
    """Mock：在线走 /debug/reset；不在线直接清库（等效于"下次启动即初始"）。"""
    try:
        r = httpx.post(f"{MOCK_BASE}/debug/reset", timeout=5)
        if r.status_code == 200 and r.json().get("ok"):
            return f"Mock 在线：已复位车位/票据/预约/记录库/虚拟时钟（{MOCK_BASE}/debug/reset）"
    except httpx.HTTPError:
        pass
    if not MOCK_DB.exists():
        return "Mock 记录库不存在：无需处理（服务启动时为初始状态）"
    with sqlite3.connect(MOCK_DB) as conn:
        for t in ("tickets", "reservations"):
            if _has_table(conn, t):
                conn.execute(f"DELETE FROM {t}")
    return "Mock 未在线：已清空 data/mock.sqlite（下次启动即初始状态）"


def reset_web() -> str:
    """Web：清空用户与对话历史，重建初始「演示用户」。"""
    if not WEB_DB.exists():
        return "data/web.sqlite 不存在：无需处理（服务启动时自动建初始用户）"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(WEB_DB) as conn:
        if _has_table(conn, "messages"):
            conn.execute("DELETE FROM messages")
        if _has_table(conn, "users"):
            conn.execute("DELETE FROM users")
        # 先重置自增序列再插入：保证演示用户编号重新从 1 开始
        if _has_table(conn, "sqlite_sequence"):
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('users', 'messages')")
        if _has_table(conn, "users"):
            conn.execute("INSERT INTO users (name, plate, created_at) VALUES (?, ?, ?)",
                         (*INITIAL_USER, now))
    return f"已清空用户与对话历史，重建「{INITIAL_USER[0]}（{INITIAL_USER[1]}）」（编号从 1 开始）"


def reset_agent_memory() -> str:
    """Agent 记忆：清空 SqliteSaver 的全部 thread 会话。"""
    if not CKPT_DB.exists():
        return "data/checkpoints.sqlite 不存在：无需处理"
    with sqlite3.connect(CKPT_DB) as conn:
        for t in ("checkpoints", "writes"):
            if _has_table(conn, t):
                conn.execute(f"DELETE FROM {t}")
    return "已清空 Agent 多轮记忆（data/checkpoints.sqlite 全部 thread）"


def reset_images() -> str:
    """运行期图片：data/captures 与 data/uploads。"""
    n = 0
    for d in IMG_DIRS:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
    return f"已清理运行期图片 {n} 个（data/captures、data/uploads）"


def main() -> int:
    ap = argparse.ArgumentParser(description="重置全部数据到初始状态")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过确认提示")
    ap.add_argument("--keep-images", action="store_true", help="保留 data/captures 与 data/uploads 图片")
    args = ap.parse_args()

    if not args.yes:
        target = "Mock 车位/记录/时钟、Web 用户与对话、Agent 记忆"
        target += "（保留图片）" if args.keep_images else "、运行期图片"
        ans = input(f"将重置：{target}。确认？(y/N) ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 1

    print(reset_mock())
    print(reset_web())
    print(reset_agent_memory())
    if not args.keep_images:
        print(reset_images())
    print("\n全部重置完成：刷新浏览器页面即可（服务无需重启）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
