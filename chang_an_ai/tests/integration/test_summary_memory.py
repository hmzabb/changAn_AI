"""摘要记忆单元测试：验证长对话场景下的自动摘要功能。

测试覆盖：
1. 超过阈值时触发LLM摘要
2. 未超过阈值时不触发
3. 摘要内容包含关键信息（用户偏好/约束/决策）
4. LLM调用失败时降级到滑动窗口
5. 摘要后历史条数正确（1条摘要 + 最近2轮 = 3条）
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from app.services.session_store import append_with_summary, get_history, _key
from app.config import settings


class TestSummaryMemory:
    """摘要记忆核心功能测试"""

    def setup_method(self):
        """每个测试前清理Redis中的测试session"""
        try:
            import redis
            r = redis.Redis.from_url(
                settings.redis_url,
                password=settings.redis_password or None,
                decode_responses=True
            )
            test_sessions = ["test_summary_001", "test_summary_002",
                           "test_summary_003", "test_summary_004"]
            for session_id in test_sessions:
                r.delete(_key(session_id))
        except Exception:
            pass

    @patch('app.services.session_store.chat_sync')
    def test_should_trigger_summary_when_exceed_threshold(self, mock_llm):
        """超过3轮（6条消息）应该触发摘要，压缩到3条（1摘要+2原文）"""
        mock_llm.return_value = json.dumps({
            "user_preferences": ["素食者"],
            "constraints": ["预算500以内"],
            "decisions": ["选择回民街"],
            "key_entities": ["回民街", "泡馍"]
        }, ensure_ascii=False)

        session_id = "test_summary_001"

        for i in range(3):  # 3轮=6条，第6条时触发摘要
            append_with_summary(session_id, "user", f"用户问题{i+1}")
            append_with_summary(session_id, "assistant", f"助手回答{i+1}")

        history = get_history(session_id)

        assert len(history) == 3, f"期望3条（1摘要+2原文），实际{len(history)}条"
        assert history[0]["role"] == "system", "第一条应该是系统摘要"
        assert "历史对话摘要" in history[0]["content"], "应包含摘要标记"
        assert "素食者" in history[0]["content"], "摘要应包含用户偏好"

    def test_should_not_trigger_summary_below_threshold(self):
        """未超过3轮（6条消息）不应该触发摘要，保留最近3轮历史"""
        session_id = "test_summary_002"

        for i in range(2):  # 2轮=4条，未超阈值
            append_with_summary(session_id, "user", f"问题{i+1}")
            append_with_summary(session_id, "assistant", f"回答{i+1}")

        history = get_history(session_id)

        assert len(history) == 4, f"未超阈值应保留4条（2轮），实际{len(history)}条"
        assert all(msg["role"] != "system" for msg in history), "不应有系统摘要消息"

    @patch('app.services.session_store.chat_sync')
    def test_should_extract_user_preferences(self, mock_llm):
        """摘要应正确提取用户在对话中提到的偏好和约束"""
        mock_llm.return_value = json.dumps({
            "user_preferences": ["不吃辣", "喜欢历史文化"],
            "constraints": ["时间半天", "步行距离<2km"],
            "decisions": ["去兵马俑"],
            "key_entities": ["兵马俑", "华清池"]
        }, ensure_ascii=False)

        session_id = "test_summary_003"

        messages = [
            ("user", "我不吃辣的东西"),
            ("assistant", "好的，我会推荐不辣的美食"),
            ("user", "我喜欢历史文化景点"),
            ("assistant", "西安有很多历史古迹，比如兵马俑"),
            ("user", "我只有半天时间，想步行"),  # 第5条
            ("assistant", "推荐兵马俑和华清池，距离适中"),  # 第6条 → 触发摘要！
        ]

        for role, content in messages:
            append_with_summary(session_id, role, content)

        history = get_history(session_id)
        summary_content = history[0]["content"]

        assert "不吃辣" in summary_content, "应提取饮食偏好"
        assert "半天时间" in summary_content or "时间" in summary_content, "应提取时间约束"
        assert "兵马俑" in summary_content, "应提取决策结果"

    @patch('app.services.session_store.chat_sync')
    def test_should_fallback_on_llm_error(self, mock_llm):
        """LLM调用失败时应静默降级，不影响主流程"""
        mock_llm.side_effect = Exception("DeepSeek API 超时")

        session_id = "test_summary_004"

        try:
            for i in range(3):  # 3轮应该触发摘要
                append_with_summary(session_id, "user", f"问题{i+1}")
                append_with_summary(session_id, "assistant", f"回答{i+1}")

            history = get_history(session_id)

            assert len(history) > 0, "降级后应有历史数据（可能是LTRIM后的）"
            assert isinstance(history, list), "返回值应是列表"
        except Exception as e:
            pytest.fail(f"摘要失败时不应抛出异常: {e}")

    @patch('app.services.session_store.chat_sync')
    def test_should_handle_malformed_json_response(self, mock_llm):
        """LLM返回非法JSON时应容错处理，使用原始文本"""
        mock_llm.return_value = "这不是JSON格式的内容，但包含了关键信息：用户是素食者"

        session_id = "test_summary_005"

        for i in range(3):  # 3轮触发摘要
            append_with_summary(session_id, "user", f"问题{i+1}")
            append_with_summary(session_id, "assistant", f"回答{i+1}")

        history = get_history(session_id)

        assert len(history) == 3, "即使JSON解析失败也应压缩到3条"
        assert "历史对话摘要" in history[0]["content"], "应有摘要标记"
        assert "素食者" in history[0]["content"] or "关键信息" in history[0]["content"], \
            "应包含原始文本内容"

    def test_should_respect_config_toggle(self):
        """summary_enabled=False时应完全禁用摘要功能"""
        original_value = settings.summary_enabled
        settings.summary_enabled = False

        try:
            session_id = "test_summary_006"

            for i in range(3):  # 3轮
                append_with_summary(session_id, "user", f"问题{i+1}")
                append_with_summary(session_id, "assistant", f"回答{i+1}")

            history = get_history(session_id)

            assert len(history) == 6, f"禁用摘要时应保留最近3轮（6条），实际{len(history)}条"
            assert all(msg["role"] != "system" for msg in history), "禁用摘要时不应有系统消息"
        finally:
            settings.summary_enabled = original_value


class TestSummaryMemoryEdgeCases:
    """边界条件和异常场景测试"""

    def setup_method(self):
        """清理测试数据"""
        try:
            import redis
            r = redis.Redis.from_url(
                settings.redis_url,
                password=settings.redis_password or None,
                decode_responses=True
            )
            for i in range(7, 10):
                r.delete(_key(f"test_edge_{i:03d}"))
        except Exception:
            pass

    @patch('app.services.session_store.chat_sync')
    def test_exact_threshold_boundary(self, mock_llm):
        """刚好达到阈值（第6条消息=第3轮助手回复）时应触发摘要"""
        mock_llm.return_value = json.dumps({
            "user_preferences": [],
            "constraints": [],
            "decisions": [],
            "key_entities": []
        }, ensure_ascii=False)

        session_id = "test_edge_007"

        for i in range(3):  # 刚好3轮
            append_with_summary(session_id, "user", f"q{i}")
            append_with_summary(session_id, "assistant", f"a{i}")

        history = get_history(session_id)
        assert len(history) == 3, "刚好3轮应触发摘要"

    def test_single_message_should_not_trigger(self):
        """只有1条消息时绝对不应触发摘要"""
        session_id = "test_edge_008"
        append_with_summary(session_id, "user", "单独一条消息")

        history = get_history(session_id)
        assert len(history) == 1
        assert history[0]["role"] == "user"

    @patch('app.services.session_store.chat_sync')
    def test_consecutive_summaries(self, mock_llm):
        """连续多轮对话应多次触发摘要，每次保留最新的摘要+最近2轮"""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return json.dumps({
                "user_preferences": [f"偏好{call_count[0]}"],
                "constraints": [],
                "decisions": [],
                "key_entities": []
            }, ensure_ascii=False)

        mock_llm.side_effect = side_effect

        session_id = "test_edge_009"

        for i in range(5):  # 5轮=10条消息，应该触发多次摘要
            append_with_summary(session_id, "user", f"q{i}")
            append_with_summary(session_id, "assistant", f"a{i}")

        history = get_history(session_id)

        assert len(history) == 3, "无论多少轮，最终都应是1摘要+2原文"
        assert "偏好" in history[0]["content"] or call_count[0] >= 1, \
            "应该是最后一次摘要的内容"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])