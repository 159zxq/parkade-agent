"""三条 API 链路验证（A0b，需真实 key）——跑通后即可群发"契约冻结 v1"。

运行：python scripts/check_api.py
前置：.env 已填 DASHSCOPE_API_KEY（或 ZHIPU_API_KEY + VISION_PROVIDER）
本脚本验证：
  1) 主脑对话（qwen-plus / deepseek-chat，OpenAI 兼容接口）
  2) 嵌入（text-embedding-v3，注意分批 ≤10 条的上限）
  3) 视觉识图（qwen-vl / glm-4v-flash，输入 docs/样例/plate_京A12345.png）
三条全绿 → A0b 完成 → 群里宣布契约 v1 冻结。
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
SAMPLE = ROOT / "docs" / "样例" / "plate_京A12345.png"


def main() -> int:
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        print("缺少 DASHSCOPE_API_KEY，请先在 .env 填写（见 .env.example）")
        return 2

    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = os.getenv("LLM_MODEL", "qwen-plus")
    failed = 0

    # 1) 主脑对话
    try:
        r = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "只回答两个字：正常"}])
        text = r.choices[0].message.content.strip()
        print(f"[PASS] 主脑对话（{model}）：{text[:20]}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 主脑对话（{model}）：{e}")
        failed += 1

    # 2) 嵌入（按 ≥10 条场景一次）
    try:
        r = client.embeddings.create(model="text-embedding-v3",
                                     input=["停车收费标准", "预约规则", "月卡办理"],
                                     dimensions=1024)
        print(f"[PASS] 嵌入 text-embedding-v3：3 条，维度 {len(r.data[0].embedding)}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 嵌入 text-embedding-v3：{e}")
        failed += 1

    # 3) 视觉识图（读样例车牌图，问它车牌号）
    try:
        b64 = base64.b64encode(SAMPLE.read_bytes()).decode()
        r = client.chat.completions.create(
            model="qwen-vl-plus",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "这张图里的车牌号码是什么？只回答车牌号。"},
            ]}])
        text = r.choices[0].message.content.strip()
        ok = "京A12345" in text
        print(f"[{'PASS' if ok else 'WARN'}] 视觉识图（qwen-vl-plus）：{text[:30]}"
              + ("" if ok else "（未识别出车牌，换 qwen-vl-flash 或 glm-4v-flash 再试）"))
        failed += 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 视觉识图：{e}")
        failed += 1

    if failed == 0:
        print("\n三条链路全绿 → A0b 完成，可宣布契约 v1 冻结")
        return 0
    print(f"\n{failed} 条链路未通过，解决后再宣布冻结")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
