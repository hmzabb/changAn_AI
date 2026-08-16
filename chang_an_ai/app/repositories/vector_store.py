"""ChromaDB 向量库封装（repository 层）。

面试考点：
- Chroma 是嵌入式单机向量库（SQLite 持久化），零运维，千级规模够用；
  本层把 Chroma API 隔离起来，将来百万级换 Milvus 只改这一个文件
  （与 java_client 隔离 httpx 同理——"依赖都收敛在 repository 层"）。
- collection 指定 hnsw:space=cosine：语义检索用余弦相似度（比方向不比长度），
  与 Java 店铺"按地理距离排序"（欧氏距离）是两个不同场景，别混。
- Windows 注意：Chroma 底层 SQLite 不允许两个进程同时写同一个库，
  所以建库用离线脚本（ingest），FastAPI 起服务后只读。
"""
from __future__ import annotations

from hashlib import sha1
from pathlib import Path

import chromadb

from app.config import BASE_DIR
from app.services.chunking import Chunk

CHROMA_DIR = BASE_DIR / "app" / "data" / "chroma"
COLLECTION_NAME = "changan_kb"


class VectorStore:
    def __init__(self, path: Path | None = None):
        # anonymized_telemetry=False：新版 chroma 的遥测客户端有 bug（capture 签名不匹配），
        # 开着会刷屏"Failed to send telemetry event"，个人项目直接关掉
        self._client = chromadb.PersistentClient(
            path=str(path or CHROMA_DIR),
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
        # cosine 建库时指定，避免默认 L2 空间影响语义检索质量
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ---------- 写入 ----------
    @staticmethod
    def _make_id(chunk: Chunk) -> str:
        """稳定唯一 id：同一条记录重跑 ingest 得到同一个 id → upsert 幂等不重复。

        结构 {source}:{type}:{业务id}:{section}:{part}:{内容哈希前6位}
        内容哈希兜底：业务 id 相同但内容变了，也能以新 id 写入而非撞车。
        """
        m = chunk.metadata
        key = ":".join(str(m.get(k, "")) for k in ("source", "type", "id", "section", "part"))
        digest = sha1(chunk.text.encode("utf-8")).hexdigest()[:6]
        return f"{key}:{digest}"

    def delete_by_source(self, source: str) -> None:
        """按 source 清空后重灌：数据删了/改了不会残留旧 chunk，保证 ingest 幂等。"""
        self._collection.delete(where={"source": source})

    def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """批量写入（upsert：同 id 覆盖）。embedding 由调用方（ingest）统一算，
        本层只管存取——单一职责，检索与写入才能共用同一套 embedding 实现。"""
        if not chunks:
            return
        self._collection.upsert(
            ids=[self._make_id(c) for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=embeddings,
            metadatas=[c.metadata for c in chunks],
        )

    # ---------- 统计 ----------
    def count(self) -> int:
        return self._collection.count()

    def source_stats(self) -> dict:
        """按 (source, type) 分组计数 —— admin/status 展示知识库构成。"""
        total = self.count()
        if total == 0:
            return {"total": 0, "by_source": {}}
        rows = self._collection.get(include=["metadatas"], limit=total)
        stats: dict[str, int] = {}
        for meta in rows["metadatas"]:
            source = str(meta.get("source", "unknown"))
            t = meta.get("type")
            key = f"{source}:{t}" if t else source
            stats[key] = stats.get(key, 0) + 1
        return {"total": total, "by_source": stats}

    # ---------- 检索 ----------
    def query(self, embedding: list[float], top_k: int = 8, where: dict | None = None) -> list[dict]:
        """相似度检索（阶段 2 RAG / 阶段 5 Agent 工具用）。

        返回 [{"text","metadata","distance"}]；
        cosine 空间下 Chroma 的 distance = 1 - 相似度，越小越相关。
        where 用于过滤，如 {"source": "java", "type": "shop"}。
        """
        res = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        docs, metas, dists = res["documents"][0], res["metadatas"][0], res["distances"][0]
        return [
            {"text": docs[i], "metadata": metas[i], "distance": dists[i]}
            for i in range(len(docs))
        ]


_store: VectorStore | None = None


def get_store() -> VectorStore:
    """进程内单例：health/status/ingest 共用同一个 Chroma 客户端。

    理由：1) SQLite 底层只允许单写者，少一个实例少一分锁冲突风险；
    2) Chroma 客户端创建有开销，health 被高频调用不值得每次新建。
    （离线 ingest 脚本是独立进程，有自己的实例，互不影响。）
    """
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
