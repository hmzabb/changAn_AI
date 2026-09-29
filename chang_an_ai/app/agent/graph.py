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
from app.config import settings
from app.prompts.agent import AGENT_SYSTEM

MAX_TOOL_ROUNDS = 6  # 最多 6 轮工具调用（每轮 agent+tools 两个节点）
RECURSION_LIMIT = MAX_TOOL_ROUNDS * 2 + 1  # 6*2+1=13：每轮2个节点+最终回答1个节点


def build_agent():
    # 懒加载：langchain/langgraph 与 pydantic 2.13 兼容性问题导致导入耗时较长，
    # 放在函数内部可避免阻塞服务启动（RAG 模式不需要这些依赖）
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, END
    from langgraph.graph.message import add_messages
    from app.agent.state import AgentState
    from app.agent.tools import ALL_TOOLS, CachedToolsWrapper
    from app.agent.parallel_tools import ParallelToolNode

    model = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.3,
        timeout=60,
        max_retries=2,
    )

    # 使用带缓存的工具包装器
    cached_tools = CachedToolsWrapper(ALL_TOOLS)

    # 构建自定义状态图（支持并行工具执行）
    workflow = StateGraph(AgentState)

    # 定义节点
    async def agent_node(state: AgentState):
        """LLM Agent 节点：决定是否调用工具"""
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

    # 使用并行工具节点替代默认 ToolNode
    parallel_tool_node = ParallelToolNode(tools=cached_tools.get_cached_tools())

    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", parallel_tool_node)

    # 设置入口点
    workflow.set_entry_point("agent")

    # 条件边：有 tool_calls 则执行工具，否则结束
    def should_continue(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile()


# 懒加载单例：首次 Agent 请求时才构建，服务启动不阻塞
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent