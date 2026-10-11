"""三个模型的独立配置（主脑 LLM / 视觉 Vision / 嵌入 Embedding）。

设计原则（A 定，2026-10-11）：**三者分别配置、禁止混用**。每个模型一组独立
环境变量 `{前缀}_API_KEY / {前缀}_BASE_URL / {前缀}_MODEL`，各自可指向不同
服务商；换任何一个不影响其余两个。全仓库取模型配置**只能**经由本模块，
不允许在业务代码里散落 `os.getenv("...API_KEY")`。

示范：
    from common.config import LLM, VISION, EMBEDDING
    client = LLM.client()                      # 主脑
    client.chat.completions.create(model=LLM.model, ...)

自检：python -c "from common.config import summary; print(summary())"
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class ModelConfig:
    """单个模型的独立配置。"""

    label: str          # llm | vision | embedding
    api_key: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def client(self, timeout: float = 30.0):
        """返回指向本配置专属 base_url/key 的 OpenAI 兼容客户端。"""
        from openai import OpenAI

        return OpenAI(api_key=self.api_key or "MISSING", base_url=self.base_url or None,
                      timeout=timeout)

    def summary(self) -> str:
        key = f"{self.api_key[:6]}…" if self.api_key else "(未配置)"
        return f"{self.label:<9} model={self.model:<22} base_url={self.base_url}  key={key}"


def _load(prefix: str, default_base: str, default_model: str) -> ModelConfig:
    return ModelConfig(
        label=prefix.lower(),
        api_key=os.getenv(f"{prefix}_API_KEY", "").strip(),
        base_url=os.getenv(f"{prefix}_BASE_URL", default_base).strip(),
        model=os.getenv(f"{prefix}_MODEL", default_model).strip(),
    )


DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

LLM = _load("LLM", DASHSCOPE_BASE, "qwen-plus")
VISION = _load("VISION", DASHSCOPE_BASE, "qwen-vl-plus")
EMBEDDING = _load("EMBEDDING", DASHSCOPE_BASE, "text-embedding-v3")

ALL: dict[str, ModelConfig] = {"llm": LLM, "vision": VISION, "embedding": EMBEDDING}

# 嵌入额外参数（建库/检索用）
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BATCH = int(os.getenv("EMBEDDING_BATCH", "10"))

# Mock 服务地址
MOCK_BASE = os.getenv("MOCK_BASE", "http://127.0.0.1:8000").strip()


def missing() -> list[str]:
    """返回未配置齐全的模型组（如 ["vision"]）。"""
    return [c.label for c in ALL.values() if not c.configured]


def summary() -> str:
    return "\n".join(c.summary() for c in ALL.values())


if __name__ == "__main__":
    print(summary())
    miss = missing()
    print("missing:", miss if miss else "(无，三组均齐全)")
