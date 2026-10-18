"""知识库建库脚本 —— 语料 → 语义切块 → 分批嵌入 → Chroma 持久化。

用法（在仓库根目录）：
    python kb/build_index.py --rebuild     # 先清后建（语料改动期建议总是加 --rebuild）
    python kb/build_index.py               # 增量 upsert（不改结构时更快）

切块规则：按 `## 小节` 语义切分，小节内 200–400 字一块、
相邻块重叠 1 句；chunk id = 小节编号 + 两位序号（如 3.2-01）；
以 _ 开头的文件（模板）不收录。
嵌入：common/config.py 的 EMBEDDING 配置组（独立配置），单批 ≤ EMBEDDING_BATCH（默认 10）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.config import EMBEDDING, EMBEDDING_BATCH  # noqa: E402

CORPUS_DIR = ROOT / "kb" / "corpus"
CHROMA_DIR = ROOT / "kb" / "chroma_db"
COLLECTION = "parking_rules"
MIN_CHARS, MAX_CHARS = 200, 400

SEC_RE = re.compile(r"^##\s+(?:([0-9]+(?:\.[0-9]+)*)\s*)?(.+?)\s*$")


def parse_sections(md_text: str) -> list[dict]:
    """按 `## ` 标题切出小节（编号 + 标题 + 正文行）。"""
    sections: list[dict] = []
    cur: dict | None = None
    for line in md_text.splitlines():
        m = SEC_RE.match(line)
        if m:
            if cur:
                sections.append(cur)
            num = m.group(1) or ""
            cur = {"num": num, "title": f"{num} {m.group(2)}".strip(), "lines": []}
        elif cur is not None:
            cur["lines"].append(line)
    if cur:
        sections.append(cur)
    return sections


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.findall(r"[^。！？；\n]+[。！？；]?", text) if s.strip()]


def make_chunks(text: str) -> list[str]:
    """小节文本 → 200–400 字块（相邻块重叠 1 句）。"""
    text = re.sub(r"\*\*", "", text)
    flat = re.sub(r"\s+", " ", text).strip()
    if not flat:
        return []
    if len(flat) <= MAX_CHARS:
        return [flat]
    chunks: list[str] = []
    cur, last = "", ""
    for s in split_sentences(flat):
        if len(cur) >= MIN_CHARS and len(cur) + len(s) > MAX_CHARS:
            chunks.append(cur.strip())
            cur = last  # 重叠：携带上一句
        cur += s
        last = s
    rest = cur.strip()
    if rest:
        if chunks and len(rest) < MIN_CHARS // 2:
            chunks[-1] = f"{chunks[-1]} {rest}"
        else:
            chunks.append(rest)
    return chunks


def collect_chunks() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for md in sorted(CORPUS_DIR.glob("*.md")):
        if md.name.startswith("_"):
            continue
        sections = parse_sections(md.read_text(encoding="utf-8-sig"))
        if not sections:
            print(f"[警告] {md.name} 里没有 '## 小节' 标题，已跳过（格式见 kb/corpus/01_总则.md）")
            continue
        for sec in sections:
            chunks = make_chunks("\n".join(sec["lines"]))
            if not chunks:
                continue
            num = sec["num"] or md.stem.split("_")[0]
            if not sec["num"]:
                print(f"[警告] 小节「{sec['title']}」没有编号，chunk 前缀暂用 {num}")
            for i, ck in enumerate(chunks, 1):
                cid = f"{num}-{i:02d}"
                if cid in seen:
                    print(f"[警告] chunk id 重复：{cid}（检查小节编号是否唯一）")
                seen.add(cid)
                items.append({"chunk_id": cid, "text": ck, "source": sec["title"],
                              "file": f"kb/corpus/{md.name}"})
    return items


def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = EMBEDDING.client(timeout=120.0).embeddings.create(
        model=EMBEDDING.model, input=texts)
    return [d.embedding for d in resp.data]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="先清空集合再建（推荐）")
    args = ap.parse_args()

    if not EMBEDDING.configured:
        print("[FAIL] 嵌入模型未配置：在 .env 的 EMBEDDING_* 块填好（见 .env.example）")
        return 2

    items = collect_chunks()
    if not items:
        print("[FAIL] kb/corpus/ 下没有可收录的语料（格式见 kb/corpus/01_总则.md）")
        return 2
    print(f"语料：{len(items)} 个 chunk 待入库")

    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if args.rebuild:
        try:
            client.delete_collection(COLLECTION)
            print(f"已清空集合 {COLLECTION}")
        except Exception:  # noqa: BLE001  集合不存在时忽略
            pass
    col = client.get_or_create_collection(name=COLLECTION,
                                          metadata={"hnsw:space": "cosine"})

    batch = max(1, min(int(EMBEDDING_BATCH or 10), 10))
    done = 0
    for start in range(0, len(items), batch):
        part = items[start:start + batch]
        try:
            vectors = embed_batch([p["text"] for p in part])
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 第 {start // batch + 1} 批嵌入失败：{e}")
            print("      检查 .env 的 EMBEDDING_* 配置与网络；修好后重跑本脚本"
                  "（--rebuild 先清后建，可安全重试）")
            return 1
        col.upsert(ids=[p["chunk_id"] for p in part],
                   embeddings=vectors,
                   documents=[p["text"] for p in part],
                   metadatas=[{"chunk_id": p["chunk_id"], "source": p["source"],
                               "file": p["file"]} for p in part])
        done += len(part)
        print(f"  已入库 {done}/{len(items)}")

    print(f"\ningested {done} chunks（集合现有 {col.count()} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
