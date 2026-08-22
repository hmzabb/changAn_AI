"""Embedding 服务：文本 → 向量。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache

from app.config import settings


class EmbeddingClient(ABC):
    """向量化抽象：入文本列表，出同长度的向量列表。"""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class SiliconflowEmbeddingClient(EmbeddingClient):
    """硅基流动 BAAI/bge-m3（OpenAI 兼容 embedding 接口）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # 消息发给大模型
        resp = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in resp.data]


@lru_cache(maxsize=1)  # 保证每次只创建一次实例
# 工厂函数
def get_embedding_client() -> EmbeddingClient:
    """工厂：创建硅基流动 embedding 客户端。"""
    return SiliconflowEmbeddingClient(
        base_url=settings.siliconflow_base_url,
        api_key=settings.siliconflow_api_key,
        model=settings.embedding_model,
    )