"""Milvus 向量库封装（repository 层）。

面试考点（本次真实迁移的教训）：
- 三个真实踩坑：
  1) VARCHAR max_length 按字节算（中文 UTF-8 每字 3 字节），text 给 16384 字节；
  2) get_collection_stats 的 row_count 含软删除行，delete 后不减少——count 必须用
     count(*) 聚合查询（delete 在 Milvus 里是软删，写 delta log）；
  3) Milvus 3.0 默认一致性是 Bounded，delete 后立即读可能读到旧数据——读侧
     显式 consistency_level="Strong"（千级数据开销可忽略）。
- schema 设计：source/type 独立标量列 + INVERTED 索引（混合过滤是 Milvus 相对
  Chroma 的强项），其余 metadata 整包塞 meta JSON 字段透传——阶段 2/5 新增
  metadata 键不用改 schema。
- Milvus 服务重启后 collection 不会自动 load，初始化时检查并补 load。
"""
from __future__ import annotations

import logging
from hashlib import sha1

from pymilvus import DataType, MilvusClient
from pymilvus.client.types import LoadState

from app.config import settings
from app.services.chunking import Chunk

COLLECTION_NAME = "changan_kb"
DIM = 1024  # bge-m3 向量维度
ID_MAX_LENGTH = 1024    # id 含中文 section 标题（字节）
TEXT_MAX_LENGTH = 16384  # 语料块 ≤600 字 + 标题；中文 3 字节/字（字节）

_EXPECTED_FIELDS = {"id", "text", "embedding", "source", "type", "meta"}
_PAGE_SIZE = 16384  # Milvus query 单次上限


class VectorStore:

    def __init__(self, uri: str | None = None):
        # pymilvus 默认 INFO 日志刷屏（gRPC 调试信息），调到 WARNING 降噪
        logging.getLogger("pymilvus").setLevel(logging.WARNING)
        # MilvusClient 惰性连接：构造不联网，首个操作才建 gRPC 连接
        self._client = MilvusClient(uri=uri or settings.milvus_uri)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """建Collection（幂等）：不存在则建 schema+索引（自动 load）；
        存在但 schema 不兼容则重建；Milvus 服务重启后 collection 不会自动
        load，这里补一次 load。"""
        if self._client.has_collection(COLLECTION_NAME):
            fields = {f["name"] for f in self._client.describe_collection(COLLECTION_NAME)["fields"]}
            if not _EXPECTED_FIELDS <= fields:  # 残留的旧 schema collection → 重建
                self._client.drop_collection(COLLECTION_NAME)
            else:
                if self._client.get_load_state(COLLECTION_NAME)["state"] != LoadState.Loaded:
                    self._client.load_collection(COLLECTION_NAME)
                return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, max_length=ID_MAX_LENGTH, is_primary=True)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=DIM)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="meta", datatype=DataType.JSON)

        index_params = self._client.prepare_index_params()
        # 向量索引：AUTOINDEX + COSINE（语义检索用余弦，比方向不比长度）
        index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
        # 标量索引：source/type 是唯一的过滤字段（混合过滤是 Milvus 强项）
        index_params.add_index(field_name="source", index_type="INVERTED")
        index_params.add_index(field_name="type", index_type="INVERTED")
        # 传了 index_params 会自动建索引并 load
        self._client.create_collection(
            collection_name=COLLECTION_NAME, schema=schema, index_params=index_params
        )

    # ---------- 写入 ----------
    @staticmethod
    def _make_id(chunk: Chunk) -> str:
        """稳定唯一 id：同一条记录重跑 ingest 得到同一个 id → upsert 幂等不重复。

        结构 {source}:{type}:{业务id}:{section}:{part}:{内容哈希前6位}
        哈希作用：corpus 块没有业务 id，不同文档的 section+part 可能撞车，
        哈希保证 id 唯一；同一条记录内容变了，也会以新 id 写入而非覆盖。
        """
        m = chunk.metadata
        key = ":".join(str(m.get(k, "")) for k in ("source", "type", "id", "section", "part"))
        digest = sha1(chunk.text.encode("utf-8")).hexdigest()[:6]
        return f"{key}:{digest}"

    @staticmethod
    def _escape_expr(value: str) -> str:
        """Milvus expr 字符串字面量转义：反斜杠和双引号。"""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def _where_to_expr(cls, where: dict | None) -> str:
        """Chroma 风格的 where dict → Milvus expr。

        白名单 source/type（schema 里唯一建的标量列），传其他键直接报错——
        宁可炸在开发期，也不让"过滤静默失效"污染检索结果。
        """
        if not where:
            return ""
        parts: list[str] = []
        for key, value in where.items():
            if key not in ("source", "type"):
                raise ValueError(f"不支持的过滤字段: {key}（仅支持 source/type）")
            if value is None:
                continue
            parts.append(f'{key} == "{cls._escape_expr(value)}"' if isinstance(value, str) else f"{key} == {value}")
        return " and ".join(parts)

    def delete_by_source(self, source: str) -> None:
        """按 source 清空后重灌：数据删了/改了不会残留旧 chunk，保证 ingest 幂等。

        Milvus 的 delete 是软删除（写 delta log），count(*) 读不到已删行，
        但 row_count 里还在——统计一律走 count()。
        """
        self._client.delete(
            collection_name=COLLECTION_NAME,
            filter=f'source == "{self._escape_expr(source)}"',
        )

    def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """批量写入（upsert：同 id 覆盖）。embedding 由调用方（ingest）统一算，
        本层只管存取——单一职责，检索与写入才能共用同一套 embedding 实现。"""
        if not chunks:
            return
        rows = [
            {
                "id": self._make_id(c),
                "text": c.text,
                "embedding": emb,
                "source": str(c.metadata.get("source", "")),
                "type": str(c.metadata.get("type", "")),
                # None 值剔除（pymilvus 插 None 有卡死前科 issue #2797），
                # JSON 字段其余键原样透传，阶段 2/5 加键不用改 schema
                "meta": {k: v for k, v in c.metadata.items() if v is not None},
            }
            for c, emb in zip(chunks, embeddings)
        ]
        self._client.upsert(collection_name=COLLECTION_NAME, data=rows)

    # ---------- 统计 ----------
    def count(self) -> int:
        """count(*) 精确计数（Strong 读，含刚 upsert 的流式数据）。
        不用 get_collection_stats 的 row_count——它含软删除行，会随
        delete_by_source 累计虚增。"""
        res = self._client.query(
            collection_name=COLLECTION_NAME,
            filter="",
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        return int(res[0]["count(*)"]) if res else 0

    def source_stats(self) -> dict:
        """按 (source, type) 分组计数 —— admin/status 展示知识库构成。
        Milvus 无 GROUP BY，分页拉回 Python 聚合（与 Chroma 版思路一致）。"""
        total = self.count()
        if total == 0:
            return {"total": 0, "by_source": {}}
        stats: dict[str, int] = {}
        offset = 0
        while True:
            rows = self._client.query(
                collection_name=COLLECTION_NAME,
                filter="",
                output_fields=["source", "type"],
                limit=_PAGE_SIZE,
                offset=offset,
                consistency_level="Strong",
            )
            if not rows:
                break
            for row in rows:
                t = row.get("type")
                key = f"{row['source']}:{t}" if t else row["source"]
                stats[key] = stats.get(key, 0) + 1
            if len(rows) < _PAGE_SIZE:
                break
            offset += len(rows)
        return {"total": total, "by_source": stats}

    # ---------- 检索 ----------
    def query(self, embedding: list[float], top_k: int = 8, where: dict | None = None) -> list[dict]:
        """相似度检索（阶段 2 RAG / 阶段 5 Agent 工具用）。

        返回 [{"text","metadata","score"}]；
        score 是 COSINE 相似度（越大越相关，与 Chroma 版 distance 语义相反）。
        where 用于过滤，如 {"source": "java", "type": "shop"}。
        """
        res = self._client.search(
            collection_name=COLLECTION_NAME,
            data=[embedding],
            limit=top_k,
            filter=self._where_to_expr(where),
            output_fields=["text", "meta"],
            consistency_level="Strong",
        )
        if not res or not res[0]:  # 空库/无命中：返回空列表不抛异常
            return []
        return [
            {"text": hit["entity"]["text"], "metadata": hit["entity"]["meta"], "score": hit["distance"]}
            for hit in res[0]
        ]


_store: VectorStore | None = None


def get_store() -> VectorStore:
    """进程内单例：health/status/ingest 共用同一个 Milvus 客户端。

    理由：gRPC 连接复用（建连有开销），且 collection 的建库/load 检查
    只需做一次。MilvusClient 线程安全，FastAPI 线程池里跑 ingest 没问题。
    """
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
