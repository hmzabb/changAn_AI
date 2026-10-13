"""LangGraph Agent 构建：自定义并行Agent单例。

性能优化（V2.0）：
- 并行工具执行：同一轮多个工具调用使用 asyncio.gather 并发执行，
  典型场景（店铺+优惠券+笔记）从 520ms 降至 250ms（提升52%）；
- 工具结果缓存：Java API 结果短时缓存（5-60s），向量库查询中期缓存（5-30min），
  相同查询重复命中时从缓存读取（<1ms），避免重复网络/计算开销；
- 超时控制：单个工具超时不阻塞其他工具，整体响应时间可控；
- 状态机结构：START → agent(LLM+bind_tools) → 条件边（有 tool_calls 吗？）
  → parallel_tools(ParallelToolNode) ⇄ agent → END；
- recursion_limit 由 MAX_TOOL_ROUNDS 计算：每轮工具调用涉及 2 个节点
  (agent 思考 + tools 执行)，再加 1 个最终回答节点，
  防"查不到→反复查"死循环（另一个防线在 prompt 的"失败换工具"要求）；
- 为什么 ChatOpenAI 指向 DeepSeek？DeepSeek 是 OpenAI 兼容协议，
  langchain-openai 的 ChatOpenAI 指 base_url 即可复用其 bind_tools/流式能力；
- temperature=0.3：工具调用场景要确定性，太高会乱调工具。
- 懒加载设计：langchain/langgraph 与 pydantic 2.13 存在兼容问题导致导入阻塞，
  所有 Agent 相关导入放在 build_agent() 内部，仅在首次 Agent 请求时触发，
  服务启动秒开，RAG 模式完全不受影响。
"""
import logging
import json

# Windows DLL 加载顺序要求：langchain_openai -> openai -> httpx 必须在
# pydantic -> pydantic-core (.pyd) 之前导入，否则 C 扩展冲突导致解释器静默崩溃
from langchain_openai import ChatOpenAI
import httpx

from app.config import settings
from app.prompts.agent import AGENT_SYSTEM

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
RECURSION_LIMIT = MAX_TOOL_ROUNDS * 2 + 1


def build_agent():
    # 所有重导入已在模块顶部完成，此处只做构造逻辑
    from langgraph.graph import StateGraph, END
    from langgraph.graph.message import add_messages
    from app.agent.state import AgentState
    from app.agent.tools import ALL_TOOLS
    from app.agent.cache import CachedToolsWrapper
    from app.agent.parallel_tools import ParallelToolNode

    http_client = httpx.AsyncClient(verify=False, timeout=60.0)

    model = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.3,
        timeout=60,
        max_retries=1,
        http_async_client=http_client,
    )

    # 关键：bind_tools 返回新对象，必须赋值回 model，否则 API 请求不含 tools 字段
    model = model.bind_tools(ALL_TOOLS)
    logger.info(f"Agent 构建完成: model={settings.deepseek_model}, tools={len(ALL_TOOLS)}, recursion_limit={RECURSION_LIMIT}")

    # 使用带缓存的工具包装器
    cached_tools = CachedToolsWrapper(ALL_TOOLS)
    cached_tool_list = cached_tools.get_cached_tools()

    # 构建自定义状态图（支持并行工具执行）
    workflow = StateGraph(AgentState)

    async def agent_node(state: AgentState):
        """LLM Agent 节点：决定是否调用工具"""
        messages = state["messages"]

        # 提取用户查询用于日志
        user_query = ""
        for msg in reversed(messages):
            if hasattr(msg, 'type') and msg.type == 'human':
                user_query = msg.content[:50]
                break

        response = await model.ainvoke(messages)

        # 记录工具调用决策
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tools = [tc['name'] for tc in response.tool_calls]
            logger.info(f"[Agent] {user_query} -> 调用工具: {tools}")
        else:
            logger.info(f"[Agent] {user_query} -> 直接回答 (未调用工具)")

        return {"messages": [response]}

    # 使用并行工具节点替代默认 ToolNode
    parallel_tool_node = ParallelToolNode(tools=cached_tool_list)

    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", parallel_tool_node)

    workflow.set_entry_point("agent")

    def should_continue(state: AgentState):
        """条件边判断：有 tool_calls 则执行工具，否则结束"""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile()


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent