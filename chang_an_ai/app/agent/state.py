"""Agent 状态定义。

面试点（LangGraph 状态机核心概念）：
- MessagesState 内置 messages 字段 + add_messages reducer——每次节点返回的新消息
  【追加】到历史而不是覆盖，这正是多轮工具调用的记忆基础；
- 自定义 iter 字段记录工具调用轮数，配合 recursion_limit 双重防死循环。
"""
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Agent 状态：messages（累加）+ iter（本轮已执行的工具调用轮数）。"""

    iter: int = 0
