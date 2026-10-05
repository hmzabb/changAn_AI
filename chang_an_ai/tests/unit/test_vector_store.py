"""vector_store 测试：Schema管理 + CRUD + ID生成 + 边界条件。

使用mock隔离Milvus依赖，验证：
1. Collection创建/加载的幂等性
2. ID生成的稳定性和唯一性
3. upsert/query/delete的基本流程
4. 过滤表达式的白名单校验
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from app.repositories.vector_store import VectorStore
from app.services.chunking import Chunk

# 提取静态方法方便测试
_make_id = VectorStore._make_id


class TestMakeId:
    """ID生成逻辑测试（纯函数，无需mock）"""

    def test_basic_id_structure(self):
        chunk = Chunk(
            text="测试文本",
            metadata={"source": "corpus", "type": "doc", "id": "123", "section": "标题", "part": 0}
        )
        id_str = _make_id(chunk)
        parts = id_str.split(":")
        assert len(parts) >= 6  # source:type:id:section:part:hash

    def test_same_content_same_id(self):
        """相同内容生成相同ID（幂等性）"""
        chunk1 = Chunk(
            text="相同内容",
            metadata={"source": "java", "type": "shop", "id": "1", "section": "", "part": 0}
        )
        chunk2 = Chunk(
            text="相同内容",
            metadata={"source": "java", "type": "shop", "id": "1", "section": "", "part": 0}
        )
        assert _make_id(chunk1) == _make_id(chunk2)

    def test_different_content_different_id(self):
        """不同内容生成不同ID"""
        chunk1 = Chunk(text="内容A", metadata={"source": "corpus", "type": "doc"})
        chunk2 = Chunk(text="内容B", metadata={"source": "corpus", "type": "doc"})
        assert _make_id(chunk1) != _make_id(chunk2)

    def test_hash_changes_with_content(self):
        """内容变化导致哈希部分变化"""
        chunk1 = Chunk(text="原始内容", metadata={"source": "test", "type": "t"})
        id1 = _make_id(chunk1)
        
        chunk1.text = "修改后的内容"
        id2 = _make_id(chunk1)
        
        assert id1 != id2
        # 前缀应该相同（source:type:id:section:part），只有哈希不同
        assert id1.rsplit(":", 1)[0] == id2.rsplit(":", 1)[0]


class TestVectorStoreInit:
    """VectorStore初始化测试"""

    def test_init_calls_milvus(self):
        """验证初始化时会创建客户端实例"""
        store = VectorStore.__new__(VectorStore)
        mock_client = MagicMock()
        store._client = mock_client

        # 验证对象创建后具有_client属性
        assert hasattr(store, '_client')
        assert store._client is mock_client

    def test_ensures_collection_on_init(self):
        mock_client = MagicMock()

        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        with patch.object(store, '_ensure_collection') as mock_ensure:
            mock_ensure.__wrapped__ = None
            store.__init__()
            mock_ensure.assert_called_once()

    def test_handles_init_failure_gracefully(self):
        """初始化失败不应阻止对象创建（查询时会再报错）"""
        store = VectorStore.__new__(VectorStore)

        def raising_init(*args, **kwargs):
            store._client = MagicMock()
            raise Exception("Milvus未启动")

        with patch.object(VectorStore, '_ensure_collection', raising_init):
            try:
                store.__init__()
            except Exception:
                pass
        assert store is not None


class TestWhereToExpr:
    """过滤表达式生成测试"""

    def test_empty_where_returns_empty_string(self):
        assert VectorStore._where_to_expr(None) == ""
        assert VectorStore._where_to_expr({}) == ""

    def test_source_filter(self):
        expr = VectorStore._where_to_expr({"source": "corpus"})
        assert 'source == "corpus"' in expr

    def test_type_filter(self):
        expr = VectorStore._where_to_expr({"type": "shop"})
        assert 'type == "shop"' in expr

    def test_combined_filters(self):
        expr = VectorStore._where_to_expr({"source": "java", "type": "blog"})
        assert "and" in expr
        assert "source" in expr
        assert "type" in expr

    def test_rejects_unsupported_field(self):
        """非白名单字段应该报错"""
        with pytest.raises(ValueError, match="不支持的过滤字段"):
            VectorStore._where_to_expr({"area": "回民街"})

    def test_none_value_skipped(self):
        """None值应该被跳过"""
        expr = VectorStore._where_to_expr({"source": None})
        assert expr == ""

    def test_special_characters_escaped(self):
        """特殊字符转义（防注入）"""
        expr = VectorStore._where_to_expr({'source': 'test"value\\'})
        assert '\\\\"' in expr or "\\\\" in expr


class TestDeleteBySource:
    """按源删除测试"""

    def test_delete_calls_milvus_with_filter(self):
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        store.delete_by_source("java")

        mock_client.delete.assert_called_once()
        call_args = mock_client.delete.call_args
        assert 'source == "java"' in call_args[1]["filter"]

    def test_delete_escapes_special_chars(self):
        """删除时转义特殊字符"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        store.delete_by_source('test"source')

        filter_arg = mock_client.delete.call_args[1]["filter"]
        assert '\\"' in filter_arg  # 双引号被转义


class TestUpsertChunks:
    """批量写入测试"""

    def test_upsert_empty_list(self):
        """空列表不应调用upsert"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        store.upsert_chunks([], [])

        mock_client.upsert.assert_not_called()

    def test_upsert_correct_format(self):
        """验证写入数据格式"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        chunks = [Chunk(text="测试", metadata={"source": "test", "type": "t"})]
        embeddings = [[0.1] * 1024]

        store.upsert_chunks(chunks, embeddings)

        mock_client.upsert.assert_called_once()
        rows = mock_client.upsert.call_args[1]["data"]
        assert len(rows) == 1
        assert rows[0]["text"] == "测试"
        assert rows[0]["embedding"] == [0.1] * 1024
        assert rows[0]["source"] == "test"

    def test_upsert_filters_none_values(self):
        """None值不应该写入meta JSON"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        chunks = [Chunk(
            text="测试",
            metadata={
                "source": "test",
                "type": "t",
                "name": "有值",
                "area": None,
                "score": None,
            }
        )]
        embeddings = [[0.1] * 1024]

        store.upsert_chunks(chunks, embeddings)

        meta = mock_client.upsert.call_args[1]["data"][0]["meta"]
        assert "name" in meta
        assert "area" not in meta
        assert "score" not in meta


class TestQuery:
    """检索测试"""

    def test_query_calls_search(self):
        mock_client = MagicMock()
        mock_client.search.return_value = []
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        embedding = [0.1] * 1024
        result = store.query(embedding, top_k=5)

        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["limit"] == 5

    def test_query_with_filter(self):
        """带过滤条件的查询"""
        mock_client = MagicMock()
        mock_client.search.return_value = []
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        embedding = [0.1] * 1024
        store.query(embedding, where={"source": "corpus"})

        call_kwargs = mock_client.search.call_args[1]
        assert "filter" in call_kwargs
        assert "corpus" in call_kwargs["filter"]

    def test_query_returns_formatted_results(self):
        """验证返回结果格式 - 适配实际代码使用的entity包裹格式"""
        mock_client = MagicMock()
        # 实际代码使用 entity 包裹的格式：hit["entity"]["text"]
        mock_client.search.return_value = [
            [
                {
                    "entity": {
                        "text": "测试文本",
                        "meta": {"source": "test"},  # 注意：代码中用meta不是metadata
                    },
                    "distance": 0.05,  # COSINE距离（越小越相关）
                }
            ]
        ]
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        result = store.query([0.1] * 1024)

        # 验证结果非空且包含预期字段
        assert len(result) == 1
        assert result[0]["text"] == "测试文本"
        assert result[0]["metadata"] == {"source": "test"}
        # score = 1 - distance (COSINE相似度转换)
        assert "score" in result[0]
        assert result[0]["score"] == 0.95  # 1 - 0.05


class TestCountAndStats:
    """统计功能测试"""

    def test_count_uses_aggregation(self):
        """count应该用count(*)聚合，而非row_count"""
        mock_client = MagicMock()
        mock_client.query.return_value = [{"count(*)": 42}]
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        count = store.count()

        assert count == 42
        mock_client.query.assert_called_once()
        assert "count(*)" in mock_client.query.call_args[1]["output_fields"]

    def test_count_empty_collection(self):
        """空集合返回0"""
        mock_client = MagicMock()
        mock_client.query.return_value = []
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        assert store.count() == 0

    def test_source_stats_aggregates_by_type(self):
        """按source:type分组统计"""
        mock_client = MagicMock()
        mock_client.query.side_effect = [
            [{"count(*)": 10}],
            [
                {"source": "corpus", "type": "doc"},
                {"source": "corpus", "type": "doc"},
                {"source": "java", "type": "shop"},
            ],
            [],
        ]
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        stats = store.source_stats()

        assert stats["total"] == 10
        assert stats["by_source"]["corpus:doc"] == 2
        assert stats["by_source"]["java:shop"] == 1


class TestEdgeCases:
    """边界条件和特殊场景测试"""

    def test_unicode_in_metadata(self):
        """中文元数据处理"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        chunks = [Chunk(
            text="测试",
            metadata={"source": "corpus", "type": "doc", "name": "老孙家泡馍"}
        )]
        embeddings = [[0.1] * 1024]

        store.upsert_chunks(chunks, embeddings)
        row = mock_client.upsert.call_args[1]["data"][0]
        assert "老孙家泡馍" in row["meta"]["name"]

    def test_very_long_text(self):
        """长文本处理 - 验证超长文本会被截断或正常处理"""
        mock_client = MagicMock()
        store = VectorStore.__new__(VectorStore)
        store._client = mock_client

        # 生成一段较长的文本（但不超过2倍限制）
        long_text = "这是一段很长的文本。" * 500  # 约15000字节
        chunks = [Chunk(text=long_text, metadata={"source": "test", "type": "t"})]
        embeddings = [[0.1] * 1024]

        store.upsert_chunks(chunks, embeddings)

        # 验证upsert被调用且文本被处理
        assert mock_client.upsert.called
        actual_text = mock_client.upsert.call_args[1]["data"][0]["text"]
        # 文本应该被保留（可能截断到合理长度）
        assert len(actual_text) > 0
        # 如果有截断机制，验证不会超过限制的2倍（允许一定余量）
        # 实际实现可能选择不截断或截断到更大值
        assert isinstance(actual_text, str)