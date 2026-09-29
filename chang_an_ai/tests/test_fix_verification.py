"""验证误判修复效果：对比修复前后的路由结果"""
import asyncio
import sys
sys.path.insert(0, "chang_an_ai")


async def test_fix_effectiveness():
    from app.routers.chat import _route, ChatRequest

    # 之前会误判的案例（现在应该正确路由到RAG）
    test_cases = [
        # ====== 高危case（之前一定会错）======
        ("兵马俑门票价格是多少？", "rag", "价格+门票 → RAG强制"),
        ("推荐几个西安必去的景点", "rag", "推荐+景点 → RAG强制"),
        ("西安有哪些老字号店值得去？", "rag", "店+老字号 → RAG强制"),
        ("陕西历史博物馆的地址在哪里？", "rag", "地址+博物馆 → RAG强制"),
        ("西安最近有什么文旅活动？", "rag", "活动 → RAG强特征词"),
        ("学生票有什么优惠政策？", "rag", "优惠+学生票 → RAG强制"),
        ("西安哪家羊肉泡馍最正宗？", "rag", "哪家好+小吃 → 需LLM判断"),

        # ====== 应该继续走Agent的case（不能误杀）======
        ("老米家泡馍店评分多少？", "agent", "评分 → Agent（正确）"),
        ("这家店有团购吗？", "agent", "团购 → Agent（正确）"),
        ("几点开门营业？", "agent", "开门 → Agent（正确）"),
        ("钟楼附近有什么好吃的？", "agent", "附近 → Agent（正确）"),
        ("哪家人均低于50元？", "agent", "人均 → Agent（正确）"),

        # ====== 边界case ======
        ("大雁塔历史", "rag", "历史 → RAG强特征"),
        ("这家店性价比如何？", "agent/rag", "复杂语义 → LLM判断"),
    ]

    print("\n" + "="*80)
    print("🔧 误判修复验证")
    print("   目标：确认之前的误判case现在能正确路由到RAG")
    print("="*80 + "\n")

    passed = 0
    failed = 0

    for message, expected, reason in test_cases:
        try:
            req = ChatRequest(session_id="test", message=message, mode="auto")
            actual = await _route(req)

            # 判断是否通过（支持多期望值）
            if "/" in expected:
                valid_results = expected.split("/")
                is_pass = actual in valid_results
            else:
                is_pass = (actual == expected)

            if is_pass:
                status = "✅"
                passed += 1
            else:
                status = "❌"
                failed += 1

            print(f"{status} | 期望: {expected:10s} | 实际: {actual:5s}")
            print(f"   输入: {message}")
            print(f"   原因: {reason}")
            print()

        except Exception as e:
            print(f"⚠️  异常: {message[:40]}")
            print(f"   错误: {e}\n")
            failed += 1

    # 统计
    total = len(test_cases)
    print("="*80)
    print(f"📊 验证结果: {passed}/{total} 通过 ({passed/total*100:.1f}%)")
    if failed > 0:
        print(f"   ⚠️  仍有 {failed} 个case未通过，需要进一步调整")
    else:
        print(f"   🎉 所有误判case已修复！")
    print("="*80)

    return failed == 0


if __name__ == "__main__":
    try:
        success = asyncio.run(test_fix_effectiveness())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n💥 验证失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)