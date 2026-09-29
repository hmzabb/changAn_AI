"""快速验证路由逻辑"""
import sys
sys.path.insert(0, "chang_an_ai")

from app.routers.chat import _route, ChatRequest

# 测试用例
test_cases = [
    ("老米家泡馍店评分多少？", "agent", "新增关键词: 评分"),
    ("这家店有团购吗？", "agent", "新增关键词: 团购"),
    ("几点开门？", "agent", "正则匹配: 几点开门"),
    ("大雁塔门票多少钱？", "agent", "正则匹配: 多少钱"),
    ("钟楼附近有什么好吃的？", "agent", "原有关键词: 附近"),
    ("大雁塔的历史文化", "rag", "知识库问题"),
]

print("\n路由逻辑验证结果:")
print("="*70)

for message, expected, reason in test_cases:
    req = ChatRequest(session_id="test", message=message, mode="auto")
    actual = _route(req)
    status = "✅" if actual == expected else "❌"
    print(f"{status} 输入: {message[:35]:35s} | 期望: {expected:4s} | 实际: {actual:4s} | {reason}")

print("="*70)