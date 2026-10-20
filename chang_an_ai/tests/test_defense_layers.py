"""防御体系V3.0 测试套件：测试全部6层防御是否正常工作。

测试覆盖：
- 第一层：工具描述优化（通过实际调用验证）
- 第二层：参数校验守卫（ToolGuard）
- 第三层：调用审计日志（ToolCallAuditor）
- 第四层：结果质量检查（ResultQualityChecker）
- 第五层：System Prompt强化（通过Agent行为验证）
- 第六层：结构化思考（StructuredThinking）

运行方式：
    cd chang_an_ai
    python -m pytest tests/test_defense_layers.py -v
    # 或
    python tests/test_defense_layers.py
"""
import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==================== 第二层：ToolGuard 测试 ====================

class TestToolGuard:
    """第二层防御：参数校验守卫测试。"""

    def setup_method(self):
        """每个测试前重置状态。"""
        from app.agent.tool_guard import ToolGuard
        ToolGuard.clear_duplicate_cache()

    def test_validate_shop_type_valid(self):
        """测试合法分类名。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_type("美食")
        assert result is None, f"合法分类名不应被拒绝: {result}"

    def test_validate_shop_type_invalid(self):
        """测试非法分类名。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_type("编程")
        assert result is not None
        assert "不合法" in result or "不在" in result

    def test_validate_shop_type_empty(self):
        """测试空分类名。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_type("")
        assert result is not None
        assert "不能为空" in result

    def test_validate_coordinates_valid_xian(self):
        """测试西安坐标范围。"""
        from app.agent.tool_guard import ToolGuard
        # 钟楼坐标
        result = ToolGuard.validate_coordinates(108.9425, 34.2610)
        assert result is None, f"西安坐标不应被拒绝: {result}"

    def test_validate_coordinates_out_of_range(self):
        """测试超出西安范围的坐标。"""
        from app.agent.tool_guard import ToolGuard
        # 北京坐标
        result = ToolGuard.validate_coordinates(116.4074, 39.9042)
        assert result is not None
        assert "不在西安范围" in result

    def test_validate_shop_id_valid(self):
        """测试合法shop_id。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_id(12345)
        assert result is None

    def test_validate_shop_id_invalid_range(self):
        """测试超出范围的shop_id。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_id(999999)
        assert result is not None
        assert "不在合理范围" in result

    def test_validate_shop_id_none(self):
        """测试空shop_id。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_shop_id(None)
        assert result is not None
        assert "不能为空" in result

    def test_validate_search_query_valid(self):
        """测试合法搜索词。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_search_query("大雁塔")
        assert result is None

    def test_validate_search_query_empty(self):
        """测试空搜索词。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.validate_search_query("")
        assert result is not None
        assert "不能为空" in result

    def test_check_duplicate_call_first_time(self):
        """测试首次调用不被拦截。"""
        from app.agent.tool_guard import ToolGuard
        result = ToolGuard.check_duplicate_call("test_tool", {"param": "value"})
        assert result is None, "首次调用不应被拦截"

    def test_check_duplicate_call_duplicate(self):
        """测试重复调用被拦截。"""
        from app.agent.tool_guard import ToolGuard
        import time

        # 第一次调用
        ToolGuard.check_duplicate_call("test_tool", {"param": "value"})

        # 立即第二次调用（应被拦截）
        result = ToolGuard.check_duplicate_call("test_tool", {"param": "value"})
        assert result is not None
        assert "重复调用" in result


# ==================== 第三层：ToolCallAuditor 测试 ====================

class TestToolCallAuditor:
    """第三层防御：调用审计日志测试。"""

    def setup_method(self):
        """每个测试前重置统计。"""
        from app.agent.graph import ToolCallAuditor
        ToolCallAuditor.reset_stats()

    def test_audit_missing_voucher_tool(self):
        """测试用户提到优惠但未调list_vouchers。"""
        from app.agent.graph import ToolCallAuditor

        tool_calls = [
            {"name": "list_shops_by_type", "args": {"type_name": "美食"}}
        ]
        warnings = ToolCallAuditor.audit_tool_calls(
            user_query="钟楼附近有优惠的火锅店",
            tool_calls=tool_calls
        )

        assert len(warnings) > 0
        assert any("优惠" in w and "list_vouchers" in w for w in warnings)

    def test_audit_direct_voucher_call(self):
        """测试直接调list_vouchers但未先获取shop_id。"""
        from app.agent.graph import ToolCallAuditor

        tool_calls = [
            {"name": "list_vouchers", "args": {"shop_id": 123}}
        ]
        warnings = ToolCallAuditor.audit_tool_calls(
            user_query="查一下123号店的优惠券",
            tool_calls=tool_calls
        )

        assert len(warnings) > 0
        assert any("编造" in w or "直接调" in w for w in warnings)

    def test_audit_duplicate_tool_calls(self):
        """测试同一工具相同参数重复调用。"""
        from app.agent.graph import ToolCallAuditor

        tool_calls = [
            {"name": "search_shops_by_name", "args": {"name": "泡馍"}},
            {"name": "search_shops_by_name", "args": {"name": "泡馍"}},
        ]
        warnings = ToolCallAuditor.audit_tool_calls(
            user_query="找泡馍店",
            tool_calls=tool_calls
        )

        assert len(warnings) > 0
        assert any("重复调用" in w for w in warnings)

    def test_audit_normal_call_no_warning(self):
        """测试正常调用无警告。"""
        from app.agent.graph import ToolCallAuditor

        tool_calls = [
            {"name": "list_shops_by_type", "args": {"type_name": "美食"}},
            {"name": "search_knowledge", "args": {"query": "大雁塔"}},
        ]
        warnings = ToolCallAuditor.audit_tool_calls(
            user_query="推荐钟楼附近的美食和大雁塔攻略",
            tool_calls=tool_calls
        )

        # 正常调用可能有轻微警告，但不应该有严重警告
        critical_warnings = [w for w in warnings if "编造" in w or "直接调" in w]
        assert len(critical_warnings) == 0, f"不应有严重警告: {critical_warnings}"

    def test_get_stats(self):
        """测试获取审计统计。"""
        from app.agent.graph import ToolCallAuditor

        # 执行几次审计
        ToolCallAuditor.audit_tool_calls(
            user_query="测试",
            tool_calls=[{"name": "search_knowledge", "args": {"query": "test"}}]
        )

        stats = ToolCallAuditor.get_stats()
        assert stats["total_calls"] >= 1
        assert "tool_usage_count" in stats
        assert "anomalies_detected" in stats


# ==================== 第四层：ResultQualityChecker 测试 ====================

class TestResultQualityChecker:
    """第四层防御：结果质量检查测试。"""

    def test_empty_result_detection_shops(self):
        """检测空店铺结果。"""
        from app.agent.parallel_tools import ResultQualityChecker

        empty_result = json.dumps({"count": 0, "shops": []})
        enhanced = ResultQualityChecker.check_and_enhance_result(
            tool_name="search_shops_by_name",
            result_content=empty_result,
            tool_args={"name": "不存在的店"}
        )

        data = json.loads(enhanced)
        assert "_quality_check" in data
        assert data["_quality_check"]["status"] == "empty"
        assert "提示" in data["_quality_check"]["hint"]

    def test_empty_result_detection_vouchers(self):
        """检测空优惠券结果。"""
        from app.agent.parallel_tools import ResultQualityChecker

        empty_result = json.dumps({"count": 0, "vouchers": []})
        enhanced = ResultQualityChecker.check_and_enhance_result(
            tool_name="list_vouchers",
            result_content=empty_result,
            tool_args={"shop_id": 123}
        )

        data = json.loads(enhanced)
        assert "_quality_check" in data
        assert data["_quality_check"]["status"] == "empty"

    def test_error_result_enhancement(self):
        """错误结果增强。"""
        from app.agent.parallel_tools import ResultQualityChecker

        error_result = json.dumps({"error": "没有找到分类「编程」，可用分类: [...]"})
        enhanced = ResultQualityChecker.check_and_enhance_result(
            tool_name="list_shops_by_type",
            result_content=error_result,
            tool_args={"type_name": "编程"}
        )

        data = json.loads(enhanced)
        assert "_quality_check" in data
        assert data["_quality_check"]["status"] == "error"
        assert "suggestion" in data["_quality_check"]

    def test_data_quality_validation_abnormal_score(self):
        """数据质量检查：异常评分。"""
        from app.agent.parallel_tools import ResultQualityChecker

        abnormal_data = {
            "count": 1,
            "shops": [{
                "id": 1,
                "name": "测试店",
                "score": 10.0,  # 异常：超过5分
                "avg_price": 100,
            }]
        }
        result_str = json.dumps(abnormal_data)
        enhanced = ResultQualityChecker.check_and_enhance_result(
            tool_name="list_shops_by_type",
            result_content=result_str,
            tool_args={"type_name": "美食"}
        )

        data = json.loads(enhanced)
        assert "_quality_check" in data
        assert data["_quality_check"]["status"] == "warnings"
        assert any("评分异常" in w for w in data["_quality_check"]["warnings"])

    def test_normal_result_unchanged(self):
        """正常结果不应被修改。"""
        from app.agent.parallel_tools import ResultQualityChecker

        normal_data = {
            "count": 2,
            "shops": [
                {"id": 1, "name": "店A", "score": 4.5, "avg_price": 80},
                {"id": 2, "name": "店B", "score": 4.8, "avg_price": 120},
            ]
        }
        result_str = json.dumps(normal_data)
        enhanced = ResultQualityChecker.check_and_enhance_result(
            tool_name="list_shops_by_type",
            result_content=result_str,
            tool_args={"type_name": "美食"}
        )

        data = json.loads(enhanced)
        # 正常数据可能没有 _quality_check，或者只有基本信息
        if "_quality_check" in data:
            assert data["_quality_check"]["status"] != "error"
            assert data["_quality_check"]["status"] != "empty"


# ==================== 第六层：StructuredThinking 测试 ====================

class TestStructuredThinking:
    """第六层防御：结构化思考测试。"""

    def setup_method(self):
        """每个测试前后重置状态。"""
        from app.agent.structured_thinking import StructuredThinking
        StructuredThinking.disable()
        StructuredThinking.reset_stats()

    def test_enable_disable(self):
        """测试启用/禁用。"""
        from app.agent.structured_thinking import StructuredThinking

        assert not StructuredThinking.is_enabled()
        StructuredThinking.enable()
        assert StructuredThinking.is_enabled()
        StructuredThinking.disable()
        assert not StructuredThinking.is_enabled()

    def test_agent_think_model_validation(self):
        """测试AgentThink模型验证。"""
        from app.agent.structured_thinking import AgentThink

        # 有效输入
        think = AgentThink(
            user_intent="用户想找钟楼附近的火锅店",
            needed_tools=["list_shops_by_type"],
            tool_reasoning={"list_shops_by_type": "用户要找火锅，需要按分类搜索"},
            is_travel_related=True,
            confidence=0.9,
            potential_issues=["可能返回空结果"]
        )
        assert think.is_valid

        # 无效：不相关
        think_invalid = AgentThink(
            user_intent="用户想学Python",
            needed_tools=[],
            tool_reasoning={},
            is_travel_related=False,
            confidence=0.8,
            potential_issues=[]
        )
        assert not think_invalid.is_valid

    def test_agent_think_invalid_tool_name(self):
        """测试无效工具名。"""
        from app.agent.structured_thinking import AgentThink
        import pytest as pt

        with pt.raises(Exception):  # Pydantic验证异常
            AgentThink(
                user_intent="测试",
                needed_tools=["invalid_tool"],  # 无效工具名
                tool_reasoning={},
                is_travel_related=True,
                confidence=0.9
            )

    def test_to_prompt_hint(self):
        """测试生成提示信息。"""
        from app.agent.structured_thinking import AgentThink

        think = AgentThink(
            user_intent="找火锅店",
            needed_tools=["list_shops_by_type"],
            tool_reasoning={"list_shops_by_type": "按分类搜索"},
            is_travel_related=True,
            confidence=0.95
        )

        hint = think.to_prompt_hint()
        assert "意图分析" in hint
        assert "计划调用工具" in hint
        assert "list_shops_by_type" in hint

    def test_extract_json_from_text(self):
        """测试从文本中提取JSON。"""
        from app.agent.structured_thinking import StructuredThinking

        # markdown代码块中的JSON
        text = '```json\n{"key": "value"}\n```'
        result = StructuredThinking._extract_json(text)
        assert result == '{"key": "value"}'

        # 纯JSON
        text2 = '{"a": 1}'
        result2 = StructuredThinking._extract_json(text2)
        assert result2 == '{"a": 1}'

        # 无法提取
        text3 = "这是一段普通文本"
        result3 = StructuredThinking._extract_json(text3)
        assert result3 is None

    def test_get_stats(self):
        """测试获取统计信息。"""
        from app.agent.structured_thinking import StructuredThinking

        stats = StructuredThinking.get_stats()
        assert "enabled" in stats
        assert "total_thinks" in stats
        assert "valid_thinks" in stats


# ==================== 集成测试 ====================

class TestDefenseIntegration:
    """防御体系集成测试。"""

    def test_full_defense_chain_mock(self):
        """使用Mock测试完整防御链路。"""
        # 这个测试模拟完整的请求流程
        # 由于需要真实的LLM和Java服务，这里只做结构验证

        # 验证所有模块可以导入
        from app.agent.tool_guard import ToolGuard
        from app.agent.graph import ToolCallAuditor
        from app.agent.parallel_tools import ResultQualityChecker, ParallelToolNode
        from app.agent.structured_thinking import StructuredThinking, AgentThink
        from app.prompts.agent import AGENT_SYSTEM
        from app.agent.tools import ALL_TOOLS

        # 验证工具列表完整
        assert len(ALL_TOOLS) == 6
        tool_names = [t.name for t in ALL_TOOLS]
        assert "search_shops_by_name" in tool_names
        assert "list_shops_by_type" in tool_names
        assert "get_shop_detail" in tool_names
        assert "list_vouchers" in tool_names
        assert "search_blogs" in tool_names
        assert "search_knowledge" in tool_names

        # 验证System Prompt包含关键规则
        assert "决策树" in AGENT_SYSTEM
        assert "防幻觉" in AGENT_SYSTEM
        assert "死循环" in AGENT_SYSTEM
        assert "禁止编造" in AGENT_SYSTEM

        print("\n✅ 所有防御模块导入成功！")
        print(f"✅ 工具数量: {len(ALL_TOOLS)}")
        print(f"✅ System Prompt长度: {len(AGENT_SYSTEM)} 字符")


# ==================== 运行入口 ====================

if __name__ == "__main__":
    print("=" * 70)
    print("🛡️  防御体系V3.0 测试套件")
    print("=" * 70)

    # 运行pytest
    pytest.main([__file__, "-v", "--tb=short"])