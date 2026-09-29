"""生产级 LangChain RAG 实现：自定义Retriever + CallbackHandler + Chain编排。

架构设计：
- MilvusRerankRetriever: 封装Milvus检索+Rerank重排为标准Retriever接口
- RAGCallbackHandler: 自定义回调，发射SSE事件（status/sources/delta/done）
- create_rag_chain(): 工厂函数，构建完整的RAG Pipeline

技术亮点：
1. 继承BaseRetriever实现自定义检索逻辑（Embedding→召回→重排）
2. 使用RunnableBranch实现条件路由（阈值判断→正常生成/Fallback）
3. 通过BaseCallbackHandler实现SSE流式事件协议
4. 支持LangSmith全链路Trace追踪
5. 保持与原手写版本完全兼容的事件格式
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
    RunnablePassthrough,
    RunnableBranch,
    ConfigurableField,
)
from langchain_openai import ChatOpenAI

from app.config import settings
from app.prompts.rag import FALLBACK_ANSWER, RAG_SYSTEM, RAG_USER_TEMPLATE
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client
from app.services.query_rewrite import rewrite
from app.services.reranker import rerank

logger = logging.getLogger(__name__)


# ============================================================================
# 自定义 Retriever：Milvus + Reranker
# ============================================================================

class MilvusRerankRetriever(BaseRetriever):
    """自定义检索器：封装 Embedding → Milvus召回 → Rerank重排。

    继承 BaseRetriever 的好处：
    1. 符合LangChain生态标准，可与其他组件无缝组合
    2. 自动获得文档类型转换、元数据处理等能力
    3. 支持LangSmith可视化追踪检索过程
    4. 未来可轻松替换底层实现（如换Chroma/Qdrant）

    内部复用现有服务层：
    - embedding.py: 文本向量化
    - vector_store.py: Milvus相似度检索
    - reranker.py: 重排+去重
    """

    def _get_relevant_documents(self,query: str,*,run_manager: Optional[Any] = None,) -> list[Document]:
        """核心检索逻辑：query → embedding → milvus → rerank → Document列表。

        使用线程池执行同步操作，避免阻塞async事件循环。

        Args:
            query: 用户查询文本（已改写后的）
            run_manager: LangChain回调管理器（用于发射事件）

        Returns:
            LangChain Document列表，包含page_content和metadata
        """
        import concurrent.futures

        def _sync_retrieve():
            """同步检索逻辑（在线程池中执行）"""
            if run_manager:
                run_manager.on_text(
                    text=f"开始检索: {query}",
                    verbose=True,
                )

            # Embedding向量化（同步HTTP调用）
            qv = get_embedding_client().embed_texts([query])[0]

            # Milvus向量检索
            hits = get_store().query(qv, top_k=settings.rag_recall_top_k)

            if run_manager:
                run_manager.on_text(
                    text=f"Milvus召回 {len(hits)} 条",
                    verbose=True,
                )

            # BGE-Reranker重排
            reranked_hits = rerank(query, hits, top_k=settings.rag_rerank_top_k)

            documents = []
            for i, hit in enumerate(reranked_hits):
                doc = Document(
                    page_content=hit["text"],
                    metadata={
                        **(hit.get("metadata") or {}),
                        "similarity": hit.get("similarity", 0),
                        "source_index": i + 1,
                    },
                )
                documents.append(doc)

            if run_manager:
                run_manager.on_text(
                    text=f"重排后保留 {len(documents)} 条",
                    verbose=True,
                )

            return documents

        try:
            # 在线程池中执行同步操作，避免阻塞async事件循环
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_sync_retrieve)
                documents = future.result(timeout=30.0)  # 30秒超时

            return documents

        except Exception as e:
            logger.error(f"检索失败: {e}", exc_info=True)
            if run_manager:
                run_manager.on_error(e)
            return []


# ============================================================================
# SSE 事件回调处理器
# ============================================================================

class RAGCallbackHandler(BaseCallbackHandler):
    """自定义回调处理器：将LangChain内部事件转换为SSE事件流。

    设计原则：
    1. 保持与前端约定的SSE协议（status/sources/delta/done/error）
    2. 在关键节点发射事件，让用户感知处理进度
    3. 收集sources信息用于前端渲染参考来源卡片
    4. 异常时优雅降级，不中断流式响应

    使用方式：
        handler = RAGCallbackHandler()
        async for chunk in chain.astream(input, config={"callbacks": [handler]}):
            async for event in handler.events():
                yield event
    """

    def __init__(self):
        super().__init__()
        self._event_queue: list[tuple[str, Any]] = []
        self._sources: list[dict] = []
        self._error: Optional[str] = None

    @property
    def sources(self) -> list[dict]:
        return self._sources

    @property
    def error(self) -> Optional[str]:
        return self._error

    def on_retriever_start(self, serialized: dict, query: str, **kwargs):
        """检索开始：通知前端'正在检索知识库'"""
        self._emit("status", "正在检索知识库…")

    def on_retriever_end(self, documents, **kwargs):
        """检索结束：提取sources信息并发射"""
        if documents:
            sources = self._build_sources_from_docs(documents)
            self._sources = sources
            self._emit("sources", sources)
        else:
            self._emit("sources", [])

    def on_llm_start(self, serialized, prompts, **kwargs):
        """LLM开始生成：通知前端状态更新"""
        self._emit("status", "正在生成回答…")

    def on_llm_new_token(self, token, **kwargs):
        """LLM输出新token：实时推送给前端"""
        if token:
            self._emit("delta", token)

    def on_llm_error(self, error, **kwargs):
        """LLM异常：记录错误信息"""
        self._error = f"AI 服务出错: {error}"
        logger.error(f"LLM错误: {error}")

    def on_chain_error(self, error, **kwargs):
        """Chain执行异常"""
        self._error = f"AI 服务出错: {error}"
        logger.error(f"Chain错误: {error}", exc_info=True)

    def _emit(self, event_type: str, data: Any):
        """内部方法：将事件加入队列"""
        self._event_queue.append((event_type, data))

    def events(self) -> list[tuple[str, Any]]:
        """获取所有已收集的事件（供外部消费）"""
        events = self._event_queue.copy()
        self._event_queue.clear()
        return events

    @staticmethod
    def _build_sources_from_docs(documents: list[Document]) -> list[dict]:
        """从Document列表构建sources事件数据（兼容原有格式）"""
        out = []
        for doc in documents:
            meta = doc.metadata
            title = meta.get("title") or meta.get("name") or meta.get("doc")
            if not title:
                title = doc.page_content.split("\n")[0][:30] or "未知来源"

            out.append({
                "index": meta.get("source_index", len(out) + 1),
                "text": doc.page_content[:80],
                "source": meta.get("source"),
                "type": meta.get("type"),
                "shop_id": meta.get("shop_id") or meta.get("id"),
                "title": title,
                "similarity": round(meta.get("similarity", 0), 3),
            })
        return out


# ============================================================================
# 辅助函数：Document格式化 & 阈值过滤
# ============================================================================

def format_docs(documents: list[Document]) -> str:
    """将Document列表格式化为Prompt上下文文本。

    格式：[1] 文本内容1\n\n[2] 文本内容2
    超长截断控制token成本。
    """
    context = "\n\n".join(
        f"[{i + 1}] {doc.page_content}"
        for i, doc in enumerate(documents)
    )
    return context[: settings.rag_max_context_chars]


def filter_by_threshold(inputs: dict) -> dict:
    """阈值过滤：移除相似度低于阈值的Document。

    返回值增加 should_fallback 标志，供下游分支使用。
    """
    documents = inputs.get("documents", [])
    valid_docs = [
        doc for doc in documents
        if doc.metadata.get("similarity", 0) >= settings.rag_min_score
    ]

    return {
        **inputs,
        "valid_documents": valid_docs,
        "should_fallback": len(valid_docs) == 0,
    }


def build_fallback_response(_: dict) -> str:
    """Fallback分支：返回兜底话术，不调用LLM。"""
    return FALLBACK_ANSWER


# ============================================================================
# 工厂函数：创建完整RAG Chain
# ============================================================================

def create_rag_chain() -> Runnable:
    """创建生产级RAG Pipeline。

    Chain结构：
    1. 输入预处理：查询改写 + 原问保留
    2. 检索阶段：MilvusRerankRetriever
    3. 过滤阶段：阈值判断
    4. 条件分支：
       - 无有效结果 → Fallback话术
       - 有有效结果 → Prompt + LLM Streaming
    5. 输出：字符串流

    特性：
    - 支持异步流式（.astream()）
    - 可观测性（LangSmith Trace）
    - 优雅降级（检索失败/低分兜底）
    - SSE事件通过CallbackHandler发射

    Returns:
        LangChain Runnable对象
    """

    retriever = MilvusRerankRetriever()

    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM),
        ("human", RAG_USER_TEMPLATE),
    ])

    llm = ChatOpenAI(
        model=settings.deepseek_model,
        streaming=True,
        temperature=0.7,
        openai_api_key=settings.deepseek_api_key,
        openai_api_base=settings.deepseek_base_url,
        timeout=60.0,
    )

    output_parser = StrOutputParser()

    # 主链路：检索 → 过滤 → 构建Prompt → LLM → 解析输出
    main_chain = (
        RunnablePassthrough.assign(
            documents=retriever
        )
        | RunnableLambda(filter_by_threshold)
        | RunnableBranch(
            (lambda x: x["should_fallback"], RunnableLambda(build_fallback_response)),
            (
                RunnablePassthrough.assign(
                    context=lambda x: format_docs(x["valid_documents"]),
                    question=lambda x: x["question"],
                )
                | prompt
                | llm
                | output_parser
            ),
        )
    )

    return main_chain


async def answer_with_langchain(query: str,history: list[dict],) -> AsyncIterator[tuple[str, Any]]:
    """基于LangChain的流式问答入口。

    Args:
        query: 用户原始问题
        history: 对话历史 [{role, content}]

    Yields:
        (event_type, payload) 元组，event_type ∈ {status, sources, delta, done, error}
    """
    handler = RAGCallbackHandler()
    chain = create_rag_chain()

    try:
        logger.info(f"[RAG] 开始处理查询: {query}")
        
        search_query = rewrite(query, history)
        logger.info(f"[RAG] 查询改写完成: {search_query}")

        logger.info("[RAG] 开始执行Chain流式调用...")
        chunk_count = 0
        async for chunk in chain.astream(
            {"question": query, "search_query": search_query},
            config={"callbacks": [handler]},
        ):
            chunk_count += 1
            if chunk_count <= 3:  # 只记录前3个chunk避免日志过多
                logger.debug(f"[RAG] 收到chunk #{chunk_count}: {type(chunk)} - {str(chunk)[:100]}")
            if isinstance(chunk, str) and chunk:
                handler._emit("delta", chunk)

        logger.info(f"[RAG] Chain执行完成，共收到 {chunk_count} 个chunks")

        if handler.error:
            yield ("error", {"message": handler.error})
        else:
            yield ("done", None)

    except Exception as e:
        logger.error(f"RAG Chain执行失败: {e}", exc_info=True)
        yield ("error", {"message": f"AI 服务出错: {e}"})

    finally:
        for event in handler.events():
            yield event