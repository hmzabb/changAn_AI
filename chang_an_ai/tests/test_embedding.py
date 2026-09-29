"""embedding 测试：客户端初始化 + 批量embedding + 错误处理。

使用mock避免真实API调用，验证：
1. 工厂函数返回正确实例
2. embed_texts 正确解析响应格式
3. API错误时的异常传播
"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.embedding import get_embedding_client, SiliconflowEmbeddingClient


class TestGetEmbeddingClient:
    """工厂函数测试"""

    def test_returns_siliconflow_client(self):
        with patch("app.services.embedding.OpenAI"):
            client = get_embedding_client()
            assert isinstance(client, SiliconflowEmbeddingClient)

    def test_lru_cache_singleton(self):
        """LRU缓存保证单例"""
        with patch("app.services.embedding.OpenAI"):
            client1 = get_embedding_client()
            client2 = get_embedding_client()
            assert client1 is client2

    def test_cache_maxsize_1(self):
        """maxsize=1：多次调用只创建一个实例"""
        with patch("app.services.embedding.OpenAI"), \
             patch.object(SiliconflowEmbeddingClient, "__init__", return_value=None) as mock_init:
            get_embedding_client()
            get_embedding_client()
            assert mock_init.call_count == 1


class TestSiliconflowEmbeddingClient:
    """Embedding客户端核心功能测试"""

    @pytest.fixture
    def client(self):
        with patch("app.services.embedding.OpenAI") as MockOpenAI:
            mock_openai_instance = MagicMock()
            MockOpenAI.return_value = mock_openai_instance
            client = SiliconflowEmbeddingClient.__new__(SiliconflowEmbeddingClient)
            client._client = mock_openai_instance
            client._model = "test-model"
            return client

    def test_embed_texts_single_text(self, client):
        """单条文本向量化"""
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
        client._client.embeddings.create.return_value = mock_response

        result = client.embed_texts(["测试文本"])
        assert len(result) == 1
        assert len(result[0]) == 1024  # bge-m3维度
        client._client.embeddings.create.assert_called_once_with(
            model="test-model",
            input=["测试文本"]
        )

    def test_embed_texts_batch(self, client):
        """批量文本向量化"""
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1] * 1024),
            MagicMock(embedding=[0.2] * 1024),
            MagicMock(embedding=[0.3] * 1024),
        ]
        client._client.embeddings.create.return_value = mock_response

        texts = ["文本1", "文本2", "文本3"]
        result = client.embed_texts(texts)
        assert len(result) == 3
        assert all(len(emb) == 1024 for emb in result)

    def test_embed_texts_preserves_order(self, client):
        """保持输入顺序"""
        import random
        embeddings = [[random.random() for _ in range(1024)] for _ in range(5)]
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=emb) for emb in embeddings]
        client._client.embeddings.create.return_value = mock_response

        texts = [f"文本{i}" for i in range(5)]
        result = client.embed_texts(texts)
        for i, emb in enumerate(result):
            assert emb == embeddings[i]

    def test_embed_texts_empty_list(self, client):
        """空列表输入"""
        mock_response = MagicMock()
        mock_response.data = []
        client._client.embeddings.create.return_value = mock_response

        result = client.embed_texts([])
        assert result == []


class TestEmbeddingErrorHandling:
    """错误处理测试"""

    def test_api_error_propagation(self):
        """API错误应该向上抛出"""
        from openai import APIError
        with patch("app.services.embedding.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.embeddings.create.side_effect = APIError("API错误")
            MockOpenAI.return_value = mock_client

            client = SiliconflowEmbeddingClient.__new__(SiliconflowEmbeddingClient)
            client._client = mock_client
            client._model = "test-model"

            with pytest.raises(APIError):
                client.embed_texts(["测试"])

    def test_network_timeout(self):
        """网络超时处理"""
        from openai import APITimeoutError
        with patch("app.services.embedding.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.embeddings.create.side_effect = APITimeoutError("请求超时")
            MockOpenAI.return_value = mock_client

            client = SiliconflowEmbeddingClient.__new__(SiliconflowEmbeddingClient)
            client._client = mock_client
            client._model = "test-model"

            with pytest.raises(APITimeoutError):
                client.embed_texts(["测试"])


class TestEmbeddingIntegrationNotes:
    """
    集成测试说明（需要真实API，不在此处运行）：
    
    运行方式：
    ```bash
    pytest tests/test_embedding.py::TestEmbeddingIntegration -s --api-key=$SILICONFLOW_KEY
    ```
    
    测试项：
    1. 真实API调用的延迟（应该<500ms/batch）
    2. 中文文本的embedding质量（相似文本的余弦相似度>0.8）
    3. 长文本截断行为（>8192 tokens是否报错或自动截断）
    """
    pass