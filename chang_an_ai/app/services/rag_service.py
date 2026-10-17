"""RAG 问答编排：前置判别 → 改写 → 检索 → 重排 → 阈值判断 → 拼 prompt → 流式生成。"""
from __future__ import annotations

from app.config import settings
from app.prompts.guard import GUARD_SYSTEM, GUARD_USER_TEMPLATE, OOD_REJECTION
from app.prompts.rag import FALLBACK_ANSWER, RAG_SYSTEM, RAG_USER_TEMPLATE
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client
from app.services.llm import chat_stream, chat_sync
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

    事件序列：前置判别 → status("正在检索") → sources / status("生成中") → delta* → done。

    前置判别（LLM 领域守卫）：
    - 用 DeepSeek-chat 做一次轻量分类（temperature=0, max_tokens=5, timeout=5s），
      判断查询是否属于「西安文旅」范畴，非领域查询直接礼貌拒答，跳过后续 RAG 管线；
    - 分类失败时降级放行（宁可漏判也不阻塞正常问答），成本约 ¥0.0002/条。

    防幻觉三板斧：
    1. prompt 强制"没有就明说" + [n] 引用标注（RAG_SYSTEM）；
    2. 相似度阈值兜底：全部低于阈值时直接返回话术、不进 LLM，
       从机制上杜绝幻觉，还省一次调用；
    3. 引用编号与 sources 事件一一对应，可溯源可跳转。

    检索用改写后的 query（指代还原），生成用用户原话（保留语气和意图）。
    """
    # ── LLM 前端判别：拦截非西安文旅查询 ──
    yield ("status", "正在分析问题…")
    try:
        resp = chat_sync(
            [
                {"role": "system", "content": GUARD_SYSTEM},
                {"role": "user", "content": GUARD_USER_TEMPLATE.format(query=query)},
            ],
            temperature=0,
            max_tokens=5,
            timeout=5.0,
        )
        is_in_domain = resp.strip() and resp.strip()[0] == "是"
    except Exception:
        # 分类失败（网络抖动/API异常）→ 降级放行，不阻塞正常问答
        is_in_domain = True

    if not is_in_domain:
        yield ("sources", [])
        yield ("delta", OOD_REJECTION)
        yield ("done", None)
        return

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
    ]
    # 把近3轮对话历史带进prompt，LLM可借此推断指代对象（兜底：指代消解漏判时不至于答错实体）
    if history:
        messages.extend(history[-6:])
    messages.append(
        {"role": "user", "content": RAG_USER_TEMPLATE.format(
            context=_build_context(valid_hits), question=query
        )},
    )
    for piece in chat_stream(messages):
        yield ("delta", piece)
    yield ("done", None)