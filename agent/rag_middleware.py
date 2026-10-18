"""RAG 上下文中间件 —— 对话前自动检索知识库并注入（检索为内建路径）。

职责：每次模型调用前，取最后一条用户消息作为 query → `retrieve(query, k=3)`
→ 拼成"规章片段"块追加到 system prompt（附 chunk id 供引用），并把命中
写入**线程局部存储**（`last_hits`），供前端/测试脚本取证。

取证实现说明（2026-10-12 实测）：langchain 1.x 的 wrap_model_call 里
`handler()` 返回的 ModelResponse 不是 dict，且写入的 metadata 不会回传到
图状态的最终消息（对象被重新构造）。故用 threading.local：
- Streamlit 每个会话一个线程 → 天然隔离（agent 实例经 cache_resource 共享）；
- CLI 单线程 → 直接可读。

设计决定（A，2026-10-12）：
- 检索挂在 `wrap_model_call` 而非做成工具——省掉"模型先调工具再答"的一整次往返，
  对话链路更快；引用出自检索结果本身，不依赖模型复述。
- 检索无结果或向量库未建时**静默跳过**（把错误记进 last_error），不阻塞对话——
  否则 C 的库没建好会把整个 Agent 拖死。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

RAG_K = 3
TOP_N_CHARS = 1800  # 注入正文上限（防止撑爆上下文）
SHORT_QUERY_CHARS = 15  # 当前问句短于此长度时，拼接上一轮用户消息一起检索


def _last_human_text(messages: list) -> str:
    """取 messages 中最后一条 HumanMessage 的文本（兼容 content 为 str 或块列表）。"""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str):
                return content.strip()
            parts = [b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if p).strip()
    return ""


def _recent_human_text(messages: list) -> str:
    """构造检索 query。

    多轮场景：像"晚上呢？""那它一天封顶多少？"这类**极短追问**自身不含
    检索线索（实测：'晚上呢？'→ 检索不到 3.3 夜间费率；'那中型车呢？ 晚上呢？'
    → 3.3-01 直达）。故当前句 < SHORT_QUERY_CHARS 时，拼接上一条用户消息
    一起检索（多轮语境化检索）。
    """
    humans: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            t = _last_human_text([msg])
            if t:
                humans.append(t)
    if not humans:
        return ""
    current = humans[-1]
    if len(current) < SHORT_QUERY_CHARS and len(humans) >= 2:
        return f"{humans[-2]} {current}"
    return current


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
    """对话前检索 + 注入。取证经线程局部存储：`last_hits` / `last_error`。

    另支持"用户上下文块"（user_context）：server 层在调用前 set_user_context()
    传入（如当前用户车辆的在场状态），中间件把它与规章片段一起拼进临时
    system prompt（不写入历史）。RAG 检索 **只用用户真实问题**，不拼上下文块。
    """

    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    # —— 取证读取（同线程内有效；Streamlit 每会话一线程）——
    @property
    def last_hits(self) -> list[dict]:
        return getattr(self._local, "hits", [])

    @property
    def last_error(self) -> str | None:
        return getattr(self._local, "error", None)

    def set_user_context(self, text: str | None) -> None:
        self._local.user_context = text

    def _time_block(self) -> str:
        """「当前时间」块：预约/计费等涉及日期的问题必须以真实当前时间为准。

        时间取自 mock 的虚拟时钟（计费演示可快进）；mock 不可达时回退系统时间。
        """
        try:
            from common.config import MOCK_BASE
            import httpx

            r = httpx.get(f"{MOCK_BASE}/debug/time", timeout=3)
            now = r.json().get("virtual_time") or ""
        except Exception:  # noqa: BLE001  mock 未启动 → 回退本机时间
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if not now:
            return ""
        return (
            "【当前时间】（系统实时数据，凡涉及「今天/明天/几号/预约时间」等日期判断，"
            "一律以此为准；用户只说到「月.日」或「几点」时，年份/日期按此推算，禁止臆造其他年份。"
            "若用户给出的日期已早于当前时间，先指出并请用户确认，不要自行改写成往年。）\n"
            f"当前时间：{now.replace('T', ' ')}"
        )

    def wrap_model_call(self, request: ModelRequest, handler: Callable):
        query = _recent_human_text(list(request.messages))
        hits: list[dict] = []
        error: str | None = None
        if query:
            try:
                from agent.rag import retrieve

                hits = retrieve(query, k=RAG_K)
            except Exception as e:  # noqa: BLE001  库未建/服务未启用 → 静默跳过
                error = str(e)

        blocks: list[str] = []
        time_block = self._time_block()
        if time_block:
            blocks.append(time_block)
        user_ctx = getattr(self._local, "user_context", None)
        if user_ctx:
            blocks.append(user_ctx)
        if hits:
            blocks.append(format_context(hits))
        if blocks:
            # 注意：SystemMessage + SystemMessage 在 langchain-core 1.x 会返回
            # ChatPromptTemplate（类型错误），必须手工拼文本后新建 SystemMessage
            base_text = _message_text(request.system_message) if request.system_message else ""
            combined = "\n\n".join([base_text, *blocks]) if base_text else "\n\n".join(blocks)
            request = request.override(system_message=SystemMessage(content=combined))

        # 取证：写入线程局部存储（写消息 metadata 实测不可靠，见模块 docstring）
        self._local.hits = hits
        self._local.error = error
        return handler(request)
