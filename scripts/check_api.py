"""三模型独立链路验证（A0b，需真实 key）——跑通后即可群发"契约冻结 v1"。

运行：python scripts/check_api.py [llm|vision|embedding]
  不带参数 = 逐块验证三组；带参数 = 只验证其中一组。
前置：.env 按 .env.example 的**三个独立配置块**填好
（LLM_* / VISION_* / EMBEDDING_*，三组互不混用）。

验证内容：
  1) LLM 主脑对话（LLM_MODEL，OpenAI 兼容接口）
  2) VISION 视觉识图（VISION_MODEL，输入 docs/样例/plate_京A12345.png）
  3) EMBEDDING 嵌入（EMBEDDING_MODEL，按 EMBEDDING_BATCH 批大小测一批）
每块独立判定：某块失败不影响其他块的验证与结论。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import EMBEDDING, EMBEDDING_BATCH, LLM, VISION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "样例" / "plate_京A12345.png"


def check_llm() -> bool | None:
    if not LLM.configured:
        print("[SKIP] 主脑：LLM_API_KEY 未配置（.env 第 1 块）")
        return None
    try:
        r = LLM.client().chat.completions.create(
            model=LLM.model, messages=[{"role": "user", "content": "只回答两个字：正常"}])
        text = r.choices[0].message.content.strip()
        print(f"[PASS] 主脑（{LLM.model} @ {LLM.base_url}）：{text[:20]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 主脑（{LLM.model} @ {LLM.base_url}）：{e}")
        return False


def check_vision() -> bool | None:
    if not VISION.configured:
        print("[SKIP] 视觉：VISION_API_KEY 未配置（.env 第 2 块）")
        return None
    try:
        b64 = base64.b64encode(SAMPLE.read_bytes()).decode()
        r = VISION.client(timeout=60.0).chat.completions.create(
            model=VISION.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "这张图里的车牌号码是什么？只回答车牌号。"},
            ]}])
        text = r.choices[0].message.content.strip()
        ok = "京A12345" in text
        print(f"[{'PASS' if ok else 'WARN'}] 视觉（{VISION.model} @ {VISION.base_url}）："
              f"{text[:30]}" + ("" if ok else "（未识别出车牌，换个 VISION_MODEL 再试）"))
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 视觉（{VISION.model} @ {VISION.base_url}）：{e}")
        return False


def check_embedding() -> bool | None:
    if not EMBEDDING.configured:
        print("[SKIP] 嵌入：EMBEDDING_API_KEY 未配置（.env 第 3 块）")
        return None
    texts = ["停车收费标准", "预约规则", "月卡办理", "夜间费率", "封顶价",
             "免费时段", "车型分类", "场内须知", "超时释放", "发票开具"][:EMBEDDING_BATCH or 10]
    try:
        r = EMBEDDING.client(timeout=60.0).embeddings.create(
            model=EMBEDDING.model, input=texts)
        dim = len(r.data[0].embedding)
        print(f"[PASS] 嵌入（{EMBEDDING.model} @ {EMBEDDING.base_url}）："
              f"一批 {len(texts)} 条，维度 {dim}")
        return True
    except Exception as e:  # noqa: BLE001
        hint = ""
        if EMBEDDING_BATCH > 10 or len(texts) > 10:
            hint = "（提示：该模型单次输入上限约 10 条，检查 EMBEDDING_BATCH）"
        print(f"[FAIL] 嵌入（{EMBEDDING.model} @ {EMBEDDING.base_url}）：{e}{hint}")
        return False


CHECKS = {"llm": check_llm, "vision": check_vision, "embedding": check_embedding}


def main() -> int:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    targets = [arg] if arg else list(CHECKS)
    unknown = [t for t in targets if t not in CHECKS]
    if unknown:
        print(f"未知参数 {unknown}，可选：llm / vision / embedding（不带给全跑）")
        return 2

    results = {t: CHECKS[t]() for t in targets}

    print("\n—— 逐块结论（互不影响）——")
    for t, ok in results.items():
        status = "✓ 通过" if ok else ("— 未配置" if ok is None else "✗ 验证未通过")
        print(f"  {t:<9} {status}")

    if all(v is True for v in results.values()):
        if len(targets) == 3:
            print("\n三组全绿 → A0b 完成，可宣布契约 v1 冻结")
        return 0
    if any(v is False for v in results.values()):
        print("\n有块验证未通过：只检查/修改对应块的环境变量，不要动其他两组配置。")
    if any(v is None for v in results.values()):
        print("\n有块尚未配置：在 .env 对应块填入 API_KEY 后重跑本脚本。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
