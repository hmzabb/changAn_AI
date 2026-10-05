"""路由逻辑测试：验证关键词启发式路由的准确率

运行方式：
    pytest tests/routing/test_routing.py -v
    # 或
    python tests/routing/test_routing.py

测试分层：
    1. Agent 正向用例：含 Agent 关键词（含冲突默认走 Agent），期望路由 agent
    2. RAG 正向用例：含 RAG 关键词或冲突规则强制 RAG，期望路由 rag
    3. 已知局限用例：真实用户说法但词表未覆盖，当前兜底 rag（盲区探测器）
    4. 显式模式：mode=agent/rag 强制路由
    5. 边界情况：空消息/打招呼/乱码等兜底 rag（带断言）
"""
import sys
import asyncio
from pathlib import Path

project_root = Path(__file__).parent.parent / "chang_an_ai"
sys.path.insert(0, str(project_root))

from app.routers.chat import _route, ChatRequest


async def async_route(req: ChatRequest) -> str:
    """异步包装函数，用于同步测试中调用异步路由"""
    return await _route(req)


# ========== 测试用例 ==========
AGENT_TEST_CASES = [
    # ---- 原有关键词（应该继续有效）----
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

    # ---- 新增关键词（修复之前的漏洞）----
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

    # ---- 正则模糊匹配（变体表达）----
    ("大雁塔门票多少钱？", "正则: 多少钱"),
    ("人均消费多少？", "正则: 多少（隐含钱）"),
    ("这家店贵不贵？", "正则: 贵不贵"),
    ("有没有便宜点的？", "正则: 便宜"),
    ("哪家人均比较低？", "正则: 哪家人均低"),
    ("早上几点开门？", "正则: 几点开门"),
    ("晚上什么时候关门？", "正则: 几点关门"),
    ("有团购优惠吗？", "正则: 有团购"),
    ("哪家店比较好？", "正则: 哪家好"),

    # ---- 补充覆盖（确保每个关键词都有用例）----
    ("我想找一家吃面的馆子", "补充: 找一家"),
    ("哪里有卖凉皮的？", "补充: 哪里有"),
    ("钟楼在哪儿？", "补充: 在哪儿"),
    ("这家店在哪个位置？", "补充: 哪个位置"),
    ("店家服务怎么样？", "补充: 店家"),
    ("有什么优惠吗？", "补充: 优惠"),
    ("最近有什么促销活动？", "补充: 促销"),
    ("一碗泡馍多少钱？", "补充: 多少钱变体"),

    # ---- 混合意图：冲突后默认走 Agent ----
    ("有没有免费景点的优惠券？", "冲突: 优惠券+免费→默认Agent"),
    ("景区门口那家店营业到几点？", "冲突: 景区+营业时间→默认Agent"),

    # ---- 容错性：关键词外的错别字/多关键词组合 ----
    ("哪家店评分怎么洋？", "容错: 关键词外错别字不影响"),
    ("钟楼附近那家面馆几点关门？", "多关键词: 附近+关门"),
    ("老孙家泡馍有团购券吗？", "组合: 团购+券"),
]

RAG_TEST_CASES = [
    # ---- 知识库问题（应该走RAG）----
    ("大雁塔的历史文化", "关键词: 历史/文化"),
    ("西安有什么特色美食？", "无关键词→兜底RAG"),
    ("回民街怎么走？", "无关键词→兜底RAG"),
    ("兵马俑参观攻略", "关键词: 参观/攻略"),
    ("陕西话怎么说？", "无关键词→兜底RAG"),
    ("西安最佳旅游季节", "关键词: 旅游"),

    # ---- 补充：RAG关键词直接命中 ----
    ("西安有什么特色小吃？", "关键词: 特色小吃"),
    ("陕西省博物馆在哪里？", "关键词: 博物馆"),
    ("去华清池的交通路线", "关键词: 交通/路线"),
    ("法门寺的历史文化", "关键词: 寺庙/历史"),
    ("免费景点有哪些？", "关键词: 免费/景点"),
    ("学生票怎么买？", "关键词: 学生票"),

    # ---- 混合意图：冲突规则强制走 RAG ----
    ("推荐一个值得参观的博物馆", "冲突: 推荐+博物馆→强制RAG"),
    ("门票价格多少钱？", "冲突: 价格+门票→强制RAG"),
]

# 已知局限：真实用户会说，但词表未覆盖，当前会兜底走 RAG。
# 这些用例是路由系统的"盲区探测器"：将来完善路由后它们会失败，
# 提示开发者把对应用例移入 AGENT_TEST_CASES。
KNOWN_LIMITATION_CASES = [
    ("哪个好一点？", "未覆盖: '哪个好'无关键词"),
    ("帮我挑一家", "未覆盖: '挑'同义表达"),
    ("回民街哪家好吃？", "未覆盖: '哪家'后无'店'"),
    ("哪家馆子味道好？", "方言: '馆子'≠'店'"),
    ("哪家点好吃？", "错别字: '点'≠'店'"),
    ("这家味道咋样？", "未覆盖: '这家'单独不成词"),
]

EDGE_CASES = [
    ("", "空消息"),
    ("你好", "纯打招呼"),
    ("谢谢", "致谢"),
    ("abc123", "无意义输入"),
    ("   ", "纯空白"),
    ("。", "纯标点"),
    ("👍👍", "纯表情"),
    ("How to get to Bell Tower", "英文输入"),
]


def test_agent_routing():
    """测试应该路由到Agent的场景（正向用例，必须全部通过）"""
    print("\n" + "="*60)
    print("✅ 测试：应该路由到 Agent 的场景")
    print("="*60)

    failed_cases = []
    for message, reason in AGENT_TEST_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = asyncio.run(async_route(req))

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
    """测试应该路由到RAG的场景（正向用例，必须全部通过）"""
    print("\n" + "="*60)
    print("✅ 测试：应该路由到 RAG 的场景")
    print("="*60)

    failed_cases = []
    for message, reason in RAG_TEST_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = asyncio.run(async_route(req))

        if result == "rag":
            print(f"  ✅ [{result.upper():4s}] {message[:40]:40s} ({reason})")
        else:
            print(f"  ❌ [{result.upper():4s}] {message[:40]:40s} ({reason}) **误判为Agent**")
            failed_cases.append((message, reason, result))

    if failed_cases:
        print(f"\n❌ 误判案例数: {len(failed_cases)}/{len(RAG_TEST_CASES)}")
        for msg, reason, actual in failed_cases:
            print(f"   - '{msg}' → 期望: rag, 实际: {actual}")
        assert False, f"有 {len(failed_cases)} 个用例路由错误"
    else:
        print(f"\n🎉 全部通过！准确率: {len(RAG_TEST_CASES)}/{len(RAG_TEST_CASES)} (100%)")


def test_known_limitations():
    """已知局限：真实用户说法但词表未覆盖，当前兜底走RAG。

    断言当前行为（rag），保证套件保持绿色；
    将来完善路由后这些用例会失败，提示移入 AGENT_TEST_CASES。
    """
    print("\n" + "="*60)
    print("⚠️  测试：已知局限（盲区探测器，当前兜底 RAG）")
    print("="*60)

    for message, reason in KNOWN_LIMITATION_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = asyncio.run(async_route(req))

        print(f"  ⚠️  [{result.upper():4s}] {message[:40]:40s} ({reason})")
        assert result == "rag", (
            f"'{message}' 已不再局限，请移入 AGENT_TEST_CASES: 实际={result}"
        )

    print(f"\n已知盲区: {len(KNOWN_LIMITATION_CASES)} 个（真实用户会误入RAG，待优化）")


def test_explicit_mode():
    """测试显式指定模式时，忽略关键词判断"""
    print("\n" + "="*60)
    print("✅ 测试：显式指定 mode 时强制路由")
    print("="*60)

    message = "钟楼附近有什么好吃的？（包含'附近'关键词）"

    req_agent = ChatRequest(session_id="test", message=message, mode="agent")
    assert asyncio.run(async_route(req_agent)) == "agent"
    print(f"  ✅ mode=agent → agent (即使有关键词)")

    req_rag = ChatRequest(session_id="test", message=message, mode="rag")
    assert asyncio.run(async_route(req_rag)) == "rag"
    print(f"  ✅ mode=rag   → rag  (即使有关键词，强制走RAG)")


def test_edge_cases():
    """测试边界情况（带断言，全部应兜底走RAG）"""
    print("\n" + "="*60)
    print("✅ 测试：边界情况")
    print("="*60)

    for message, reason in EDGE_CASES:
        req = ChatRequest(session_id="test", message=message, mode="auto")
        result = asyncio.run(async_route(req))

        print(f"  ✅ [{result.upper():4s}] '{message[:20]:20s}' ({reason})")
        assert result == "rag", f"'{message}' ({reason}) 应该兜底走RAG，实际={result}"


if __name__ == "__main__":
    print("\n" + "#"*60)
    print("#  路由逻辑测试套件")
    print("#  测试文件: tests/routing/test_routing.py")
    print("#"*60)

    try:
        test_agent_routing()
        test_rag_routing()
        test_known_limitations()
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
