"""Embedding 服务：文本 → 向量。

设计要点（面试必考）：
- DeepSeek 不提供 embedding 接口 → 主实现用硅基流动 BAAI/bge-m3
  （OpenAI 兼容协议，openai SDK 指 base_url 即可调用，阶段 2 调 DeepSeek 还用它）。
- 抽象基类 EmbeddingClient + 工厂 get_embedding_client()：依赖倒置，
  换实现不动业务代码（测试用 hash、断网降级用本地模型）。
- 铁律：入库与检索必须用同一个实现（同一向量空间），换实现必须重建库——
  ingest 按 source 删除重建，重跑一遍即可完成切换。
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

import jieba

from app.config import settings

DIM = 1024  # bge-m3 向量维度，所有实现必须保持一致


class EmbeddingClient(ABC):
    """向量化抽象：入文本列表，出同长度的向量列表。"""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class SiliconflowEmbeddingClient(EmbeddingClient):
    """主实现：硅基流动 BAAI/bge-m3（OpenAI 兼容 embedding 接口）。

    用 openai SDK 而非手写 httpx：SDK 自带鉴权头/重试/错误分类，且整个项目
    调 DeepSeek（对话）和 SiliconFlow（embedding）共用一套依赖与心智模型。
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 延迟导入：hash 兜底模式下不触发网络依赖

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # 一次请求传多条文本（batch）：省 API 调用次数、省费用、吞吐更高
        resp = self._client.embeddings.create(model=self._model, input=texts)
        # 返回顺序与 input 一致，直接按序映射
        return [item.embedding for item in resp.data]


class LocalHashEmbeddingClient(EmbeddingClient):
    """开发兜底：jieba 分词 + 词哈希 → 确定性稀疏向量（无网络、免费、可复现）。

    定位要诚实：这只是让全流程"跑得通"的开发替身，语义检索质量远不如真模型。
    面试被问"没 API key 怎么开发调试"就说这个；单测也靠它的确定性做断言。
    """

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * DIM
            for word in jieba.cut(text):
                word = word.strip()
                if not word:
                    continue
                h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
                for k in range(3):  # 每个词打 3 个维度，降低碰撞
                    idx = (h + k * 0x9E3779B9) % DIM  # 黄金比例常数扩散
                    vec[idx] += 1.0 if (h >> (k * 8)) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])  # L2 归一化，cosine 空间下更稳
        return vectors


class LocalSentenceTransformersEmbeddingClient(EmbeddingClient):
    """TODO(关键部分-你来完成)：本地 sentence-transformers 兜底实现。

    背景（面试话术）：bge-m3 有开源权重，本地能跑同款模型 → 向量空间与 API 版
    完全一致，断网/欠费时无缝切换。这正是选 bge-m3 而非其他闭源 embedding 的理由。

    实现提示：
    1. `pip install sentence-transformers`（依赖 torch，体积较大，装在 .venv 里）；
    2. 模型权重：国内网络建议从 ModelScope 下载
       （`modelscope` 包 snapshot_download('BAAI/bge-m3') 拿本地路径），
       然后 SentenceTransformer(本地路径)；直连 HuggingFace 大概率超时；
    3. encode 参数 `normalize_embeddings=True`（bge 系列官方推荐，cosine 检索更准）；
    4. 模型加载慢（秒级），在 __init__ 里预加载，别每次请求都加载；
    5. 完成后把 .env 里 EMBEDDING_PROVIDER=local，验证一条文本输出 1024 维向量。
    """

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("这是留给你的关键部分，实现提示见类注释")


def get_embedding_client() -> EmbeddingClient:
    """工厂：按 EMBEDDING_PROVIDER 选实现（依赖倒置——上层只认抽象）。

    provider 取值：siliconflow（默认）| local | hash
    siliconflow 未配 key 时自动降级 hash，保证开发流程不因 key 卡死。
    """
    provider = settings.embedding_provider
    if provider == "hash":
        return LocalHashEmbeddingClient()
    if provider == "local":
        return LocalSentenceTransformersEmbeddingClient()
    if not settings.siliconflow_api_key:
        print("[embedding] 警告：未配置 SILICONFLOW_API_KEY，降级为本地 hash 向量（仅供开发调试）")
        return LocalHashEmbeddingClient()
    return SiliconflowEmbeddingClient(
        base_url=settings.siliconflow_base_url,
        api_key=settings.siliconflow_api_key,
        model=settings.embedding_model,
    )
