"""LangGraph Agent 构建：create_react_agent 单例。

面试点：
- 状态机结构：START → agent(LLM+bind_tools) → 条件边（有 tool_calls 吗？）
  → tools(ToolNode) ⇄ agent → END，由 create_react_agent 预构建好；
  messages 用 add_messages reducer 累加，天然支持多轮工具调用；
- recursion_limit=12：约等于最多 6 轮工具调用（每轮 2 个节点），
  防"查不到→反复查"死循环（另一个防线在 prompt 的"失败换工具"要求）；
- 为什么 ChatOpenAI 指向 DeepSeek？DeepSeek 是 OpenAI 兼容协议，
  langchain-openai 的 ChatOpenAI 指 base_url 即可复用其 bind_tools/流式能力；
- temperature=0.3：工具调用场景要确定性，太高会乱调工具。
"""
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.agent.tools import ALL_TOOLS
from app.config import settings
from app.prompts.agent import AGENT_SYSTEM

MAX_TOOL_ROUNDS = 6  # 最多 6 轮工具调用（每轮 agent+tools 两个节点）


def build_agent():
    model = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.3,
        timeout=60,
        max_retries=2,
    )
    return create_react_agent(model, ALL_TOOLS, prompt=AGENT_SYSTEM)


# 进程内单例：图结构构建有开销，且 LangGraph 图对象线程安全
agent = build_agent()
