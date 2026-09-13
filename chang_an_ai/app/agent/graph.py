"""LangGraph Agent 构建：create_react_agent 单例。

面试点：
- 状态机结构：START → agent(LLM+bind_tools) → 条件边（有 tool_calls 吗？）
  → tools(ToolNode) ⇄ agent → END，由 create_agent 预构建好；
  messages 用 add_messages reducer 累加，天然支持多轮工具调用；
- recursion_limit=12：约等于最多 6 轮工具调用（每轮 2 个节点），
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


def build_agent():
    # 懒加载：langchain/langgraph 与 pydantic 2.13 兼容性问题导致导入耗时较长，
    # 放在函数内部可避免阻塞服务启动（RAG 模式不需要这些依赖）
    from langchain_openai import ChatOpenAI
    from langchain.agents import create_agent
    from app.agent.tools import ALL_TOOLS

    model = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.3,
        timeout=60,
        max_retries=2,
    )
    return create_agent(model, ALL_TOOLS, system_prompt=AGENT_SYSTEM)


# 懒加载单例：首次 Agent 请求时才构建，服务启动不阻塞
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent