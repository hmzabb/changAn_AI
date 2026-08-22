"""检索重排：召回 top8 → 重排 top4。

为什么需要重排（面试必考）：向量相似度只是"语义相关性"的粗略近似，对
"回民街有哪些好吃的泡馍店"这类问题，embedding 召回的前 8 条可能被同一篇
文章的多个小节占满（互相重复），或漏掉高精度匹配项。重排用规则信号
（关键词重叠/商圈命中）+ MMR 去重，把最该进 prompt 的 4 条挑出来——
prompt 上下文预算有限（rag_max_context_chars），喂给 LLM 的每条都必须值得。

API 版重排增强：SiliconFlow 的 bge-reranker-v2-m3（免费 API），把 (query, 每个候选文本)
成对送入交叉编码器，输出精确相关性分数，通常能再提升召回质量 5-10 个点。
网络异常时自动降级回规则重排（面试点：优雅降级）。
"""
from __future__ import annotations

import logging

import httpx
import jieba

from app.config import settings

logger = logging.getLogger(__name__)

LAMBDA = 0.6  # MMR 权重：分数与多样性之间取 6:4（分数优先，去重兜底）


def _tokens(text: str) -> set[str]:
    # 为什么返回 set 而不是 list？因为后续用 query_tokens & _tokens(text) 做集合交集计算 Jaccard 重叠率，
    # 集合的交集操作是 O(n) 哈希查找，比列表 O(n²) 快得多。
    """jieba 分词取词集（长度>1 的词才有区分度）。"""
    return {w.strip() for w in jieba.cut(text) if len(w.strip()) > 1}


# ============================================================================
# API 重排客户端：交叉编码器精确打分
# ============================================================================

class SiliconflowRerankerClient:
    """硅基流动重排 API 客户端（交叉编码器 bge-reranker-v2-m3）。

    面试点：为什么不用 openai SDK？openai SDK 的 rerank 接口目前还在 beta
    阶段且不同厂商的 rerank 请求/响应格式不统一，直接用 httpx 更可控。
    SiliconFlow 的 /v1/rerank 接口与 Jina AI 的 rerank API 协议兼容。

    使用方式：作为上下文管理器（with），自动复用 httpx 连接池。
    """

    RERANK_PATH = "/rerank"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=(base_url or settings.siliconflow_base_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key or settings.siliconflow_api_key}",
                # 声明请求体为 JSON 格式
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        """关闭底层 httpx 连接池，释放资源。"""
        self._client.close()

    def __enter__(self):
        """进入上下文管理器，返回自身。"""
        return self

    def __exit__(self, *exc):
        """退出上下文管理器，自动关闭连接池。"""
        self.close()

    def rerank(self, query: str, documents: list[str], top_n: int = 4) -> list[dict]:
        """调用 SiliconFlow /v1/rerank，返回 [{index, relevance_score}, ...] 按分数降序。
        这个方法把"调用远端 API → 拿回精确分数"这件事封装成一个函数调用，上层不需要关心 HTTP 细节。

        Args:
            query: 用户查询文本
            documents: 候选文档列表，每条对应一个 hit 的 text 字段
            top_n: 返回前 N 条结果

        Returns:
            [{"index": 0, "relevance_score": 0.95}, ...]
            index 对应 documents 列表中的原始位置，
            relevance_score 是交叉编码器对 (query, doc) 的精确语义相关性分数。

        请求体示例：
            {"model": "BAAI/bge-reranker-v2-m3", "query": "...", "documents": [...]}
        响应体示例：
            {"results": [{"index": 0, "relevance_score": 0.95}]}
        """
        payload = {
            "model": settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        resp = self._client.post(self.RERANK_PATH, json=payload)
        resp.raise_for_status()
        body = resp.json()
        return body.get("results", [])


# ============================================================================
# MMR 去重：贪心选择"得分高且与已选内容不重复"的条目
# ============================================================================

def _mmr_select(scored: list[tuple[float, dict]], top_k: int) -> list[dict]:
    """MMR 贪心去重：从已排序的 (score, hit) 列表中选出 top_k 条。

    核心思想：每次迭代选择"得分 × λ - 冗余度 × (1-λ)"最高的条目，
    避免选出的 top_k 全是同一篇文章的相邻小节（冗余内容浪费 prompt 预算）。

    Args:
        scored: 已按分数降序排列的 [(score, hit), ...] 列表
        top_k: 最终返回的条目数

    Returns:
        去重后的 top_k 条 hit 列表
    """
    selected: list[dict] = []
    selected_tokens: list[set[str]] = []
    remaining = scored[:]  # 浅拷贝，避免修改调用方传入的列表

    while remaining and len(selected) < top_k:
        best, best_idx, best_mmr = None, 0, -1.0
        for i, (score, h) in enumerate(remaining):
            toks = _tokens(h.get("text", ""))
            # 冗余度：当前候选与已选条目中最大 Jaccard 重叠率
            redundancy = max(
                (len(toks & s) / max(len(toks), 1) for s in selected_tokens),
                default=0.0,
            )
            mmr = LAMBDA * score - (1 - LAMBDA) * redundancy
            if mmr > best_mmr:
                best_mmr, best, best_idx = mmr, h, i
        selected.append(best)
        selected_tokens.append(_tokens(best.get("text", "")))
        remaining.pop(best_idx)

    return selected


# ============================================================================
# 规则重排：零成本基线，关键词重叠 + 商圈命中加权
# ============================================================================

def _rule_based_rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """规则重排：关键词重叠率 + 商圈命中加权 → MMR 去重。

    分数公式：向量相似度 + 0.3 × 关键词重叠率 + 0.2 × 商圈命中
    - 关键词重叠率：query 与 hit 文本的 jieba 分词交集比例
    - 商圈命中：用户 query 中明确出现的商圈名（如"回民街"），
      与该商圈关联的店铺额外加分，提升本地化搜索精度

    Args:
        query: 用户查询文本
        hits: Milvus 召回结果 [{text, metadata, distance, similarity}, ...]
        top_k: 最终返回条数

    Returns:
        重排 + 去重后的 top_k 条 hit
    """
    query_tokens = _tokens(query)
    scored: list[tuple[float, dict]] = []
    for h in hits:
        text = h.get("text", "")
        overlap = len(query_tokens & _tokens(text)) / max(len(query_tokens), 1)
        score = h["similarity"] + 0.3 * overlap
        area = (h.get("metadata") or {}).get("area")
        if area and area in query:  # 用户点名商圈（如"回民街"）时，同商圈店铺显著加分
            score += 0.2
        scored.append((score, h))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _mmr_select(scored, top_k)


# ============================================================================
# API 重排：交叉编码器精确打分
# ============================================================================

def _api_rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """API 重排：交叉编码器精确打分 → MMR 去重。

    流程：
    1. 提取所有 hit 的 text 字段，组装为 documents 列表
    2. 调用 SiliconFlow /v1/rerank 获取交叉编码器精确相关性分数
    3. 用 API 返回的 relevance_score 替换原始向量相似度，再走 MMR 去重

    面试点：为什么 API 分数之后还要 MMR？
    交叉编码器衡量的是 (query, doc) 的语义相关性，不关心文档之间的重复度。
    如果召回的前 8 条有 4 条来自同一篇文章的不同小节，API 会给它们都打高分，
    MMR 负责剔除冗余，确保喂给 LLM 的每条上下文都是差异化的。

    Args:
        query: 用户查询文本
        hits: Milvus 召回结果 [{text, metadata, distance, similarity}, ...]
        top_k: 最终返回条数

    Returns:
        交叉编码器打分 + MMR 去重后的 top_k 条 hit
    """
    documents = [h.get("text", "") for h in hits]
    with SiliconflowRerankerClient() as client:
        results = client.rerank(query, documents, top_n=len(documents))

    # 构建 index → relevance_score 的映射（API 返回的 index 对应 documents 列表原始位置）
    score_map: dict[int, float] = {r["index"]: r["relevance_score"] for r in results}

    # 用 API 精确分数替换原始向量相似度，组装 scored 列表
    scored: list[tuple[float, dict]] = []
    for i, h in enumerate(hits):
        api_score = score_map.get(i, 0.0)  # API 未返回的文档默认 0 分
        scored.append((api_score, h))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _mmr_select(scored, top_k)


# ============================================================================
# 对外入口：自动选择 API 重排 / 规则重排，异常时优雅降级
# ============================================================================

def rerank(query: str, hits: list[dict], top_k: int = 4) -> list[dict]:
    """检索重排入口：API 重排（开启时）→ 规则重排（降级/默认）。

    Args:
        query: 用户查询文本
        hits: Milvus 召回结果 [{text, metadata, distance, similarity}, ...]
        top_k: 最终返回条数

    Returns:
        重排 + 去重后的 top_k 条 hit

    面试点-优雅降级：
    settings.rerank_enabled=True 时优先走 API 交叉编码器，网络异常
    （httpx.HTTPError）/JSON 解析失败（ValueError）/响应字段缺失（KeyError）
    时自动回退到规则重排，保证用户请求不中断。这是生产环境的标准实践：
    不因外部依赖故障而影响核心链路可用性。
    """
    if len(hits) <= top_k:
        return hits

    if settings.rerank_enabled:
        try:
            return _api_rerank(query, hits, top_k)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            # 网络不可达、超时、4xx/5xx → httpx.HTTPError
            # 响应体非 JSON → ValueError
            # 响应缺少 "results" 或 "index" 字段 → KeyError
            logger.warning("API 重排失败，降级为规则重排: %s", e)
            return _rule_based_rerank(query, hits, top_k)

    return _rule_based_rerank(query, hits, top_k)