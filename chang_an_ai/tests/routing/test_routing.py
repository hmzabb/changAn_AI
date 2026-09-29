"""路由逻辑测试：验证关键词启发式路由的准确率

运行方式：
    pytest tests/test_routing.py -v
    # 或
    python tests/test_routing.py
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent / "chang_an_ai"
sys.path.insert(0, str(project_root))

from app.routers.chat import _route, ChatRequest


# ========== 测试用例 ==========
AGENT_TEST_CASES = [
    # 原有关键词（应该继续有效）
    ("帮我找钟楼附近的泡馍店", "包含'帮我找'"),
    ("钟楼附近有什么好吃的？", "包含'附近'"),
    ("哪家人均低于50元？", "包含'人均'"),
    ("这家店有优惠券吗？", "包含'优惠券'"),
    ("有什么券可以领？", "包含'券'"),
    ("钟楼有什么店推荐？", "包含'有什么店'"),
    ("哪家泡馍店好吃？", "包含'哪家店'"),
    ("这家店铺怎么样？", "包含'店铺'"),
    ("回民街探店体验", "包含'探店'"),
    ("推荐一家好吃的店", "包含'推荐'"),

    # 新增关键词（修复之前的漏洞）
    ("老米家泡馍店评分多少？", "新增: 评分"),
    ("这家店星级是几星？", "新增: 星级"),
    ("用户好评多不多？", "新增: 好评"),
    ("口碑怎么样？", "新增: 口碑"),
    ("几点开门营业？", "新增: 开门"),
    ("什么时候关门？", "新增: 关门"),
    ("营业时间到晚上几点？", "新增: 营业时间"),
    ("店铺地址在哪里？", "新增: 地址"),
    ("电话号码是多少？", "新增: 电话"),
    ("有联系方式吗？", "新增: 联系方式"),
    ("这家店有团购吗？", "新增: 团购"),
    ("现在有什么折扣活动？", "新增: 折扣/活动"),
    ("价格贵不贵？", "新增: 价格"),

    # 正则模糊匹配（变体表达）
    ("大雁塔门票多少钱？", "正则: 多少钱"),
    ("人均消费多少？", "正则: 多少（隐含钱）"),
    ("这家店贵不贵？", "正则: 贵不贵"),
    ("有没有便宜点的？", "正则: 便宜"),
    ("哪家人均比较低？", "正则: 哪家人均低"),
    ("早上几点开门？", "正则: 几点开门"),
    ("晚上什么时候关门？", "正则: 几点关门"),
    ("有团购优惠吗？", "正则: 有团购"),
    ("哪家店比较好？", "正则: 哪家好"),
    ("哪个好一点？", "正则: 哪个好"),
]

RAG_TEST_CASES = [
    # 知识库问题（应该走RAG）
    ("大雁塔的历史文化", "景点历史文化"),
    ("西安有什么特色美食？", "美食特色"),
    ("回民街怎么走？", "交通指南"),
    ("兵马俑参观攻略", "旅游攻略"),
    ("陕西话怎么说？", "方言文化"),
    ("西安最佳旅游季节", "旅游建议"),
]


def test_agent_routing():
    """测试应该路由到Agent的场景"""
    print("\n" + "="*60)
    print("✅ 测试：应该路由到 Agent 的场景")
    print("="*60)

    failed_cases = []
    for message, reason in AGENT_TEST_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = _route(req)

        if result == "agent":
            print(f"  ✅ [{result.upper():4s}] {message[:40]:40s} ({reason})")
        else:
            print(f"  ❌ [{result.upper():4s}] {message[:40]:40s} ({reason}) **失败**")
            failed_cases.append((message, reason, result))

    if failed_cases:
        print(f"\n❌ 失败案例数: {len(failed_cases)}/{len(AGENT_TEST_CASES)}")
        for msg, reason, actual in failed_cases:
            print(f"   - '{msg}' → 期望: agent, 实际: {actual}")
        assert False, f"有 {len(failed_cases)} 个用例路由错误"
    else:
        print(f"\n🎉 全部通过！准确率: {len(AGENT_TEST_CASES)}/{len(AGENT_TEST_CASES)} (100%)")


def test_rag_routing():
    """测试应该路由到RAG的场景"""
    print("\n" + "="*60)
    print("✅ 测试：应该路由到 RAG 的场景")
    print("="*60)

    failed_cases = []
    for message, reason in RAG_TEST_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = _route(req)

        if result == "rag":
            print(f"  ✅ [{result.upper():4s}] {message[:40]:40s} ({reason})")
        else:
            print(f"  ❌ [{result.upper():4s}] {message[:40]:40s} ({reason}) **误判为Agent**")
            failed_cases.append((message, reason, result))

    if failed_cases:
        print(f"\n⚠️  误判案例数: {len(failed_cases)}/{len(RAG_TEST_CASES)}")
        print("   （这些case可能需要LLM兜底来判断）")
    else:
        print(f"\n🎉 全部通过！准确率: {len(RAG_TEST_CASES)}/{len(RAG_TEST_CASES)} (100%)")


def test_explicit_mode():
    """测试显式指定模式时，忽略关键词判断"""
    print("\n" + "="*60)
    print("✅ 测试：显式指定 mode 时强制路由")
    print("="*60)

    message = "钟楼附近有什么好吃的？（包含'附近'关键词）"

    req_agent = ChatRequest(session_id="test", message=message, mode="agent")
    assert _route(req_agent) == "agent"
    print(f"  ✅ mode=agent → agent (即使有关键词)")

    req_rag = ChatRequest(session_id="test", message=message, mode="rag")
    assert _route(req_rag) == "rag"
    print(f"  ✅ mode=rag   → rag  (即使有关键词，强制走RAG)")


def test_edge_cases():
    """测试边界情况"""
    print("\n" + "="*60)
    print("✅ 测试：边界情况")
    print("="*60)

    edge_cases = [
        ("", "空消息应该走RAG"),
        ("你好", "纯打招呼应该走RAG"),
        ("谢谢", "致谢应该走RAG"),
        ("abc123", "无意义输入应该走RAG"),
    ]

    for message, reason in edge_cases:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = _route(req)
        status = "✅" if result == "rag" else "❌"
        print(f"  {status} [{result.upper():4s}] '{message[:20]:20s}' ({reason})")


if __name__ == "__main__":
    print("\n" + "#"*60)
    print("#  路由逻辑测试套件")
    print("#  测试文件: tests/test_routing.py")
    print("#"*60)

    try:
        test_agent_routing()
        test_rag_routing()
        test_explicit_mode()
        test_edge_cases()

        print("\n" + "="*60)
        print("🎉 所有测试完成！")
        print("="*60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)