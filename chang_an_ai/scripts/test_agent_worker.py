"""Agent 测试 worker：每个测试用例独立进程运行，避免 Windows DLL 泄漏。
用法: python test_agent_worker.py "<query>"
输出: JSON 到 stdout（含 tool_calls, answer, elapsed_ms）
"""
import sys
import time
import json

query = sys.argv[1]

sys.path.insert(0, '.')

from app.agent.graph import get_agent, RECURSION_LIMIT
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import asyncio

async def run():
    agent = get_agent()
    t0 = time.perf_counter()
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        config={"recursion_limit": RECURSION_LIMIT},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    msgs = result.get("messages", [])
    tool_calls = []
    for m in msgs:
        if isinstance(m, AIMessage) and hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                tool_calls.append({
                    "name": tc.get("name", "unknown"),
                    "args": tc.get("args", {}),
                })

    answer = ""
    for m in msgs:
        if isinstance(m, AIMessage) and m.content:
            answer = m.content if isinstance(m.content, str) else str(m.content)

    output = {
        "success": bool(answer),
        "elapsed_ms": elapsed_ms,
        "tool_calls": tool_calls,
        "answer": answer[:3000],  # 截断长回答
        "error": None,
    }
    print(json.dumps(output, ensure_ascii=False))

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as e:
        print(json.dumps({"success": False, "elapsed_ms": 0, "tool_calls": [], "answer": "", "error": str(e)}, ensure_ascii=False))