"""RAG 上下文中间件 —— 对话前自动检索知识库并注入（契约 §6「检索为内建路径」）。

职责：每次模型调用前，取最后一条用户消息作为 query → `retrieve(query, k=3)`
→ 拼成"规章片段"块追加到 system prompt（附 chunk id 供引用），并写入
消息 metadata（`rag_hits`），供前端/测试脚本取证。

设计决定（A，2026-10-12）：
- 检索挂在 `wrap_model_call` 而非做成工具——省掉"模型先调工具再答"的一整次往返，
  对话链路更快；引用出自检索结果本身，不依赖模型复述。
- 检索无结果或向量库未建时**静默跳过**（把错误写进 metadata），不阻塞对话——
  否则 C 的库没建好会把整个 Agent 拖死。
"""
from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

RAG_K = 3
TOP_N_CHARS = 1800  # 注入正文上限（防止撑爆上下文）


def _last_human_text(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str):
                return content.strip()
            parts = [b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if p).strip()
    return ""


def format_context(hits: list[dict]) -> str:
    lines = [
        "【规章片段】（来自停车场规章知识库，回答规章类问题时必须以此为准并标注引用编号；",
        "与用户问题无关则忽略；没有相关内容时如实说明无法从规章中确认，禁止编造。）",
    ]
    for h in hits:
        lines.append(f"[{h['chunk_id']}]（{h['source']}）{h['text'][:TOP_N_CHARS]}")
    return "\n".join(lines)


def _message_text(msg) -> str:
    """提取 SystemMessage / str 的文本（兼容 content 为 str 或块列表）。"""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") in ("text", None))
    return str(content)


class RagContextMiddleware(AgentMiddleware):
    """对话前检索 + 注入。metadata 输出：rag_hits / rag_error（供测试与前端取证）。"""

    def wrap_model_call(self, request: ModelRequest, handler: Callable):
        query = _last_human_text(list(request.messages))
        hits: list[dict] = []
        error: str | None = None
        if query:
            try:
                from agent.rag import retrieve

                hits = retrieve(query, k=RAG_K)
            except Exception as e:  # noqa: BLE001  库未建/服务未启用 → 静默跳过
                error = str(e)

        if hits:
            # 注意：SystemMessage + SystemMessage 在 langchain-core 1.x 会返回
            # ChatPromptTemplate（类型错误），必须手工拼文本后新建 SystemMessage
            rag_text = format_context(hits)
            base_text = _message_text(request.system_message) if request.system_message else ""
            combined = f"{base_text}\n\n{rag_text}" if base_text else rag_text
            request = request.override(system_message=SystemMessage(content=combined))

        response = handler(request)
        try:  # 取证：挂在回复消息的 metadata 上
            if isinstance(response, dict):  # ExtendedModelResponse
                msg = response.get("result")
            else:
                msg = response
            meta = getattr(msg, "response_metadata", None)
            if isinstance(meta, dict):
                meta["rag_hits"] = [h["chunk_id"] for h in hits]
                meta["rag_error"] = error
        except Exception:  # noqa: BLE001  取证失败不影响主流程
            pass
        return response


def rag_trace(messages: list) -> tuple[list[str], str | None]:
    """从对话消息序列中提取最近一次检索取证（测试脚本用）。"""
    for msg in reversed(messages):
        meta = getattr(msg, "response_metadata", None)
        if isinstance(meta, dict) and "rag_hits" in meta:
            return meta.get("rag_hits") or [], meta.get("rag_error")
    return [], None


def empty_metadata_keys() -> dict[str, Any]:
    return {"rag_hits": [], "rag_error": None}
