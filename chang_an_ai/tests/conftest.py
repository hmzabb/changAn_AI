"""Pytest fixtures：共享的mock和测试数据。"""
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def sample_chunks():
    """标准测试用Chunk列表"""
    from app.services.chunking import Chunk
    
    return [
        Chunk(
            text="大雁塔位于西安市南郊，是唐代建筑，高64.5米。",
            metadata={
                "source": "corpus",
                "type": "doc",
                "doc": "西安景点介绍",
                "section": "大雁塔",
                "part": 0,
            }
        ),
        Chunk(
            text="【老孙家泡馍】是一家位于西安回民街商圈的美食店，地址：北院门118号。",
            metadata={
                "source": "java",
                "type": "shop",
                "id": 1,
                "name": "老孙家泡馍",
                "area": "回民街",
            }
        ),
        Chunk(
            text="用户游客发布探店笔记《回民街美食攻略》：泡馍凉皮肉夹馍都很好吃（点赞100，评论20）",
            metadata={
                "source": "java",
                "type": "blog",
                "id": 10,
                "shop_id": 1,
                "title": "回民街美食攻略",
            }
        ),
    ]


@pytest.fixture
def sample_embeddings():
    """标准测试用embedding向量（1024维）"""
    import random
    random.seed(42)  # 固定随机种子保证可重复性
    return [[random.random() for _ in range(1024)] for _ in range(3)]


@pytest.fixture
def mock_milvus_client():
    """Mock Milvus客户端（用于VectorStore测试）"""
    with pytest.mock.patch("app.repositories.vector_store.MilvusClient") as MockMilvus:
        mock_client = MagicMock()
        MockMilvus.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_embedding_client():
    """Mock Embedding客户端"""
    with pytest.mock.patch("app.services.embedding.OpenAI") as MockOpenAI:
        mock_openai = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
        mock_openai.embeddings.create.return_value = mock_response
        MockOpenAI.return_value = mock_openai
        yield mock_openai


@pytest.fixture
def sample_rerank_hits():
    """Reranker测试用的候选结果"""
    return [
        {"text": "大雁塔与慈恩寺的历史沿革介绍", "metadata": {"area": None}, "similarity": 0.55},
        {"text": "回民街美食指南：泡馍凉皮肉夹馍", "metadata": {"area": "回民街"}, "similarity": 0.55},
        {"text": "无关文本占位", "metadata": {"area": None}, "similarity": 0.50},
        {"text": "西安城墙骑行攻略", "metadata": {"area": "城墙"}, "similarity": 0.58},
    ]