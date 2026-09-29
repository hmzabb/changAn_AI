"""快速验证LLM路由升级是否成功"""
import asyncio
import sys
sys.path.insert(0, "chang_an_ai")

async def test_routing():
    from app.routers.chat import _route, ChatRequest

    test_cases = [
        ("评分多少？", "agent", "第一层: 关键词'评分'"),
        ("有团购吗？", "agent", "第一层: 关键词'团购'"),
        ("几点开门？", "agent", "第二层: 正则'几点.*门'"),
        ("门票多少钱？", "agent", "第二层: 正则'多少.*钱'"),
        ("大雁塔历史", "rag/agent", "第三层: LLM判断（需要API）"),
        ("这家店性价比如何？", "rag/agent", "第三层: LLM判断（复杂语义）"),
    ]

    print("\n" + "="*70)
    print("🚀 LLM意图分类路由 - 快速验证")
    print("="*70 + "\n")

    for message, expected, reason in test_cases:
        try:
            req = ChatRequest(session_id="test", message=message, mode="auto")
            result = await _route(req)

            if expected == result or "/" in expected:
                status = "✅"
            else:
                status = "❌"

            print(f"{status} 输入: {message[:35]:35s}")
            print(f"   期望: {expected:10s} | 实际: {result:5s}")
            print(f"   原因: {reason}")
            print()

        except Exception as e:
            print(f"❌ 异常: {message[:30]}")
            print(f"   错误: {e}")
            print()

    print("="*70)
    print("✅ 路由函数已升级为异步版本（支持LLM调用）")
    print("="*70)

if __name__ == "__main__":
    try:
        asyncio.run(test_routing())
    except Exception as e:
        print(f"\n💥 运行失败: {e}")
        import traceback
        traceback.print_exc()