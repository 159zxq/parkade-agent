"""三模型独立链路验证（A0b，需真实 key）——跑通后即可群发"契约冻结 v1"。

运行：python tests/check_api.py [llm|vision|embedding]
  不带参数 = 逐块验证三组；带参数 = 只验证其中一组。
前置：.env 按 .env.example 的**三个独立配置块**填好
（LLM_* / VISION_* / EMBEDDING_*，三组互不混用）。

接口模式（*_API_MODE）：
  auto（默认）= 先按标准 Chat Completions 调用；若网关明确要求 OpenAI
  Responses API（/v1/responses），本脚本自动切换重试并在结论里提示你把
  .env 加一行 `LLM_API_MODE=responses`（之后 Agent 代码按该模式组装）。
  chat / responses 为强制指定。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import EMBEDDING, EMBEDDING_BATCH, LLM, VISION, ModelConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "样例" / "plate_京A12345.png"

# auto 回退时的提示收集，最后统一打印
_HINTS: list[str] = []


def _try_chat_then_responses(cfg: ModelConfig, chat_fn, responses_fn):
    """按 api_mode 调用；auto 模式在网关要求 Responses 时自动切换。"""
    mode = cfg.api_mode
    if mode == "responses":
        return responses_fn()
    if mode == "chat":
        return chat_fn()
    # auto
    try:
        return chat_fn()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Responses API" in msg or "responses" in msg.lower():
            result = responses_fn()
            _HINTS.append(f"{cfg.label.upper()}_API_MODE=responses"
                          f"（本网关要求 Responses API，已在 auto 模式下自动切换通过）")
            return result
        raise


def check_llm() -> bool | None:
    if not LLM.configured:
        print("[SKIP] 主脑：LLM_API_KEY 未配置（.env 第 1 块）")
        return None
    client = LLM.client(timeout=120.0)

    def via_chat():
        r = client.chat.completions.create(
            model=LLM.model, messages=[{"role": "user", "content": "只回答两个字：正常"}])
        return r.choices[0].message.content.strip()

    def via_responses():
        r = client.responses.create(model=LLM.model, input="只回答两个字：正常")
        return (r.output_text or "").strip()

    try:
        text = _try_chat_then_responses(LLM, via_chat, via_responses)
        print(f"[PASS] 主脑（{LLM.model} @ {LLM.base_url}，mode={LLM.api_mode}）：{text[:20]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 主脑（{LLM.model} @ {LLM.base_url}）：{e}")
        return False


def check_vision() -> bool | None:
    if not VISION.configured:
        print("[SKIP] 视觉：VISION_API_KEY 未配置（.env 第 2 块）")
        return None
    client = VISION.client(timeout=180.0)
    b64 = base64.b64encode(SAMPLE.read_bytes()).decode()
    data_url = f"data:image/png;base64,{b64}"
    question = "这张图里的车牌号码是什么？只回答车牌号。"

    def via_chat():
        r = client.chat.completions.create(
            model=VISION.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": question},
            ]}])
        return r.choices[0].message.content.strip()

    def via_responses():
        r = client.responses.create(
            model=VISION.model,
            input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": data_url},
                {"type": "input_text", "text": question},
            ]}])
        return (r.output_text or "").strip()

    try:
        text = _try_chat_then_responses(VISION, via_chat, via_responses)
        ok = "京A12345" in text
        print(f"[{'PASS' if ok else 'WARN'}] 视觉（{VISION.model} @ {VISION.base_url}，"
              f"mode={VISION.api_mode}）：{text[:30]}"
              + ("" if ok else "（未识别出车牌，换个 VISION_MODEL 再试）"))
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
        r = EMBEDDING.client(timeout=120.0).embeddings.create(
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

    if _HINTS:
        print("\n建议把下面几行加进 .env（本次已自动回退通过，下次起固定走该模式）：")
        for h in _HINTS:
            print(f"  {h}")

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
