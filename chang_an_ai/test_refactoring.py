"""快速验证重构后的代码是否正常工作"""

import sys
sys.path.insert(0, '.')

def test_vocabularies():
    """测试词库配置"""
    from app.intent.vocabularies import get_vocabularies

    vocab = get_vocabularies()

    # 测试获取时间词库
    time_rt, time_hist = vocab.get_time_vocabs()
    assert "现在" in time_rt
    assert "去年" in time_hist
    print("✅ 词库配置加载成功")

    # 测试获取所有词库
    all_vocabs = vocab.get_all_vocabs()
    assert len(all_vocabs) == 12  # 12个词库集合
    print(f"✅ 共加载 {len(all_vocabs)} 个词库")


def test_routing_rules():
    """测试路由规则配置"""
    from app.intent.routing_rules import get_routing_rules

    rules = get_routing_rules()

    # 测试获取规则列表
    rule_list = rules.get_rules()
    assert len(rule_list) >= 8  # 至少有8条规则
    print(f"✅ 路由规则加载成功，共 {len(rule_list)} 条规则")

    # 测试按名称获取规则
    first_rule = rules.get_rule_by_name(rule_list[0]["name"])
    assert first_rule is not None
    assert "action" in first_rule
    print(f"✅ 规则查询功能正常：{first_rule['name']}")


def test_feature_extractor():
    """测试特征提取器"""
    from app.intent.feature_extractor import get_feature_extractor

    extractor = get_feature_extractor()

    # 测试实时意图识别
    features = extractor.extract("现在这家店价格怎么样")
    assert features.time_intent.value == "realtime"
    assert features.data_type.value == "pricing"
    print(f"✅ 特征提取正常: {features}")

    # 测试历史意图识别
    features2 = extractor.extract("去年博物馆的历史")
    assert features2.time_intent.value == "historical"
    assert features2.subject_type.value == "attraction"
    print(f"✅ 历史意图识别正常: {features2}")


def test_router():
    """测试路由决策引擎"""
    from app.intent.router import get_intent_router
    from app.intent.feature_extractor import get_feature_extractor

    router = get_intent_router()
    extractor = get_feature_extractor()

    # 测试实时价格查询 → 应该走Agent
    features = extractor.extract("现在这家店多少钱")
    decision = router.route(features)
    assert decision.action == "agent"
    print(f"✅ 路由决策正常: {decision.action} (置信度: {decision.confidence})")

    # 测试历史查询 → 应该走RAG
    features2 = extractor.extract("去年景点的历史")
    decision2 = router.route(features2)
    assert decision2.action == "rag"
    print(f"✅ 历史查询路由正常: {decision2.action} (置信度: {decision2.confidence})")


def test_prompt_templates():
    """测试Prompt模板"""
    from app.intent.prompt_templates import get_intent_prompts

    prompts = get_intent_prompts()

    # 测试获取V4 Prompt
    prompt = prompts.get_classification_prompt(version="v4")
    assert "你是一个高级意图识别专家" in prompt
    assert "{question}" in prompt
    print(f"✅ Prompt模板加载成功，长度: {len(prompt)} 字符")


def main():
    print("=" * 60)
    print("🚀 开始验证重构后的代码")
    print("=" * 60)

    try:
        test_vocabularies()
        test_routing_rules()
        test_feature_extractor()
        test_router()
        test_prompt_templates()

        print("=" * 60)
        print("🎉 所有测试通过！重构成功！")
        print("=" * 60)
        return 0

    except Exception as e:
        print("=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())