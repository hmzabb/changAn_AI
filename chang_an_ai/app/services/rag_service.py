"""RAG 问答编排：改写 → 检索 → 重排 → 阈值判断 → 拼 prompt → 流式生成。"""
from __future__ import annotations

from app.config import settings
from app.prompts.rag import FALLBACK_ANSWER, RAG_SYSTEM, RAG_USER_TEMPLATE
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client
from app.services.llm import chat_stream
from app.services.query_rewrite import rewrite
from app.services.reranker import rerank

def retrieve(query: str) -> list[dict]:
    """检索：embedding → 向量库召回 top8 → 重排 top4。

    返回 [{text, metadata, similarity, milvus_sim}]：
    - similarity: 交叉编码器重排后的精确相关性分数（用于排序和展示）
    - milvus_sim: Milvus COSINE 相似度（用于阈值闸门判断，避免重排分数壓
      缩导致有效文档被误伤）

    embedding 或 Milvus 异常时返回空列表，由上层 fallback 话术兜底——
    检索失败不阻塞问答，用户至少能得到"暂无相关信息"的回复。
    """
    try:
        qv = get_embedding_client().embed_texts([query])[0]
        hits = get_store().query(qv, top_k=settings.rag_recall_top_k)
    except Exception:
        return []
    hits = [dict(h, similarity=h["score"], milvus_sim=h["score"]) for h in hits]
    # 原始召回阶段过滤明显噪声，减少进入重排的候选数
    hits = [h for h in hits if h["milvus_sim"] >= settings.rag_recall_min_score]
    if not hits:
        return []
    reranked = rerank(query, hits, top_k=min(settings.rag_rerank_top_k, len(hits)))
    return reranked


def _build_context(hits: list[dict]) -> str:
    """把重排后的片段拼成 prompt 上下文，超长截断（控制 token 成本）。"""
    context = "\n\n".join(f"[{i + 1}] {h['text']}" for i, h in enumerate(hits))
    return context[: settings.rag_max_context_chars]


def _build_sources(hits: list[dict]) -> list[dict]:
    """sources 事件数据：前端渲染"参考来源"卡片（shop_id 用于跳店铺详情页）。

    title 兜底：voucher 等元数据没有标题的片段，取正文首行做标题，
    避免前端卡片出现空白标题。
    """
    out = []
    for i, h in enumerate(hits):
        title = h["metadata"].get("title") or h["metadata"].get("name") or h["metadata"].get("doc")
        if not title:
            title = h["text"].split("\n")[0][:30] or "未知来源"
        out.append({
            "index": i + 1,
            "text": h["text"][:80],
            "source": h["metadata"].get("source"),
            "type": h["metadata"].get("type"),
            "shop_id": h["metadata"].get("shop_id") or h["metadata"].get("id"),
            "title": title,
            "similarity": round(h["similarity"], 3),
        })
    return out


def answer(query: str, history: list[dict]):
    """流式回答：生成 (event, payload) 序列。

    事件序列：status("正在检索") → sources / status("生成中") → delta* → done。
    比之前多一个 status 事件：前端收到后更新进度文案，用户不会觉得"卡死了"。

    防幻觉三板斧：
    1. prompt 强制"没有就明说" + [n] 引用标注（RAG_SYSTEM）；
    2. 相似度阈值兜底：全部低于阈值时直接返回话术、不进 LLM，
       从机制上杜绝幻觉，还省一次调用；
    3. 引用编号与 sources 事件一一对应，可溯源可跳转。

    检索用改写后的 query（指代还原），生成用用户原话（保留语气和意图）。
    """
    yield ("status", "正在检索知识库…")
    search_query = rewrite(query, history)
    hits = retrieve(search_query)
    # 用 Milvus COSINE 相似度做闸门判断（重排分数是交叉编码器的另一套量纲，
    # 可能与原始余弦值相差很大，直接对重排分卡阈值会把好文档误杀）。
    valid_hits = [h for h in hits if h.get("milvus_sim", h.get("similarity", 0)) >= settings.rag_min_score]

    if not valid_hits:
        yield ("sources", [])
        yield ("delta", FALLBACK_ANSWER)
        yield ("done", None)
        return

    yield ("sources", _build_sources(valid_hits))
    yield ("status", "正在生成回答…")
    messages = [
        {"role": "system", "content": RAG_SYSTEM},
        {"role": "user", "content": RAG_USER_TEMPLATE.format(
            context=_build_context(valid_hits), question=query
        )},
    ]
    for piece in chat_stream(messages):
        yield ("delta", piece)
    yield ("done", None)