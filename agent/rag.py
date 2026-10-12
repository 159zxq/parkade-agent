"""RAG 检索路径 —— 冻结签名，见 docs/接口契约.md §6。

retrieve(query, k) 返回 top-k 规章片段（检索不做生成；生成由主脑在 Agent 内完成）。
Agent 与评测脚本（kb/eval_rag.py）共用本函数；内部实现由 A 维护，
其它模块只许调用、不许改签名。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common.config import EMBEDDING

ROOT = Path(__file__).resolve().parents[1]
CHROMA_DIR = ROOT / "kb" / "chroma_db"
COLLECTION = "parking_rules"

_col = None


def _collection():
    global _col
    if _col is None:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        try:
            _col = client.get_collection(COLLECTION)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "向量库未就绪：请先在仓库根目录运行 python kb/build_index.py --rebuild"
            ) from e
    return _col


def embed_text(text: str) -> list[float]:
    if not EMBEDDING.configured:
        raise RuntimeError("嵌入模型未配置：检查 .env 的 EMBEDDING_* 块")
    resp = EMBEDDING.client(timeout=60.0).embeddings.create(
        model=EMBEDDING.model, input=[text])
    return resp.data[0].embedding


def retrieve(query: str, k: int = 3) -> list[dict[str, Any]]:
    """检索 top-k 规章片段。

    返回：[{"chunk_id": "3.2-01", "text": "...", "score": 0.83,
            "source": "3.2 分时段费率标准", "file": "kb/corpus/03_费率与计费规则.md"}, ...]
    （相似度降序）
    """
    col = _collection()
    res = col.query(query_embeddings=[embed_text(query)], n_results=k,
                    include=["documents", "metadatas", "distances"])
    out = []
    for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                    res["metadatas"][0], res["distances"][0]):
        out.append({"chunk_id": cid, "text": doc,
                    "score": round(1.0 - float(dist), 4),  # cosine 空间：相似度 = 1 − 距离
                    "source": meta.get("source", ""), "file": meta.get("file", "")})
    return out
