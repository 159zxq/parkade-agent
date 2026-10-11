"""Agent 组装 —— create_agent + SqliteSaver 多轮记忆（A1）。

注意（2026-10-11 实测）：langgraph 1.2+ 的 create_react_agent 已弃用，
改用 langchain.agents.create_agent；接口模式经 common/config.py 的
LLM.langchain_kwargs() 注入（responses/chat 双支持）。

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
from agent.tools import ALL_TOOLS
from common.config import LLM

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "checkpoints.sqlite"


def build_agent(db_path: str | Path = DEFAULT_DB):
    """组装带落盘记忆的 Agent（同一 thread_id 的历史跨进程保持）。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    model = ChatOpenAI(**LLM.langchain_kwargs(), timeout=120)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    return create_agent(model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT,
                        checkpointer=saver)


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


def chat(agent, thread_id: str, message: str) -> str:
    """发一条消息，返回最终文本回复。"""
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [{"role": "user", "content": message}]},
                          config=config)
    return _content_to_text(result["messages"][-1].content)
