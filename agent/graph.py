"""Agent 组装 —— create_agent + SqliteSaver 多轮记忆 + RAG 中间件（A1/A3）。

注意（2026-10-11 实测）：langgraph 1.2+ 的 create_react_agent 已弃用，
改用 langchain.agents.create_agent；接口模式经 common/config.py 的
LLM.langchain_kwargs() 注入（responses/chat 双支持）。
A3（10-12）：挂载 RagContextMiddleware——对话前自动检索规章并注入 system prompt。

用法：
    from agent.graph import build_agent, chat
    agent = build_agent()
    print(chat(agent, "user-001", "A区还有多少车位？"))
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.prompts import SYSTEM_PROMPT
from agent.rag_middleware import RagContextMiddleware
from agent.tools import ALL_TOOLS
from common.config import LLM

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "checkpoints.sqlite"

# 最近一次 build_agent 创建的 RAG 中间件（chat_with_trace 取证用；
# Streamlit 每会话一线程，中间件内部用 threading.local，天然隔离）
_rag_middleware: RagContextMiddleware | None = None


def build_agent(db_path: str | Path = DEFAULT_DB, *, with_rag: bool = True):
    """组装带落盘记忆的 Agent（同一 thread_id 的历史跨进程保持）。

    with_rag=False 时不挂检索中间件（离线调试/对照实验用）。
    """
    global _rag_middleware
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # max_retries：网关限流(429)/瞬时错误自动退避重试，答辩现场更稳
    model = ChatOpenAI(**LLM.langchain_kwargs(), timeout=120, max_retries=3)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    middleware: list = []
    if with_rag:
        _rag_middleware = RagContextMiddleware()
        middleware.append(_rag_middleware)
    else:
        _rag_middleware = None
    return create_agent(model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT,
                        middleware=middleware, checkpointer=saver)


def _content_to_text(content) -> str:
    """responses 模式下 content 可能是块列表，抽取文本部分。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p).strip()


def chat_with_trace(agent, thread_id: str, message: str) -> tuple[str, list[dict]]:
    """发一条消息，返回 (文本回复, 本轮检索命中的片段列表)。

    片段列表取自 RAG 中间件的线程局部存储（见 agent/rag_middleware.py），
    供前端折叠展示引用。
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [{"role": "user", "content": message}]},
                          config=config)
    text = _content_to_text(result["messages"][-1].content)
    snippets = _rag_middleware.last_hits if _rag_middleware is not None else []
    return text, snippets


def chat_with_context(agent, thread_id: str, message: str,
                      extra_context: str | None = None) -> tuple[str, list[dict]]:
    """带附加上下文的对话（如当前用户的车辆状态块）。

    extra_context 注入在临时 system prompt 层（不写入历史）；RAG 检索仍用
    用户真实问题（不拼上下文，避免污染检索语义，见 rag_middleware 说明）。
    """
    config = {"configurable": {"thread_id": thread_id}}
    context = {"user_context": extra_context} if extra_context else None
    if _rag_middleware is not None:
        _rag_middleware.set_user_context(extra_context)
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": message}]},
                              config=config, context=context)
    finally:
        if _rag_middleware is not None:
            _rag_middleware.set_user_context(None)
    text = _content_to_text(result["messages"][-1].content)
    snippets = _rag_middleware.last_hits if _rag_middleware is not None else []
    return text, snippets


def chat(agent, thread_id: str, message: str) -> str:
    """发一条消息，返回最终文本回复。"""
    return chat_with_trace(agent, thread_id, message)[0]
