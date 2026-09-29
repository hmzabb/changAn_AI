"""query_rewrite 测试：启发式短路 + LLM改写 + 容错机制。

核心验证：
1. 无指代词 → 直接返回原问题（短路优化）
2. 有指代词 → 调用LLM改写
3. LLM失败/空输出 → 回退原问题（永不阻塞）
"""
import pytest
from app.services import query_rewrite


class TestNeedsRewrite:
    """needs_rewrite 启发式判断测试"""

    def test_pronoun_it(self):
        assert query_rewrite.needs_rewrite("它几点关门？") is True

    def test_pronoun_this(self):
        assert query_rewrite.needs_rewrite("这家店怎么样？") is True

    def test_pronoun_here(self):
        assert query_rewrite.needs_rewrite("这里有什么好吃的？") is True

    def test_no_pronoun(self):
        assert query_rewrite.needs_rewrite("大雁塔的历史？") is False

    def test_empty_question(self):
        assert query_rewrite.needs_rewrite("") is False

    def test_partial_match(self):
        """'它'作为子串但不是独立词"""
        assert query_rewrite.needs_rewrite("其他的问题") is True  # '其他'包含'它'子串


class TestRewriteShortCircuit:
    """无指代词时的短路优化（不调用LLM）"""

    def test_no_rewrite_needed(self, monkeypatch):
        """无指代词 → 原样返回，不调用chat_sync"""
        called = []
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: called.append(1) or "改写后")
        result = query_rewrite.rewrite("大雁塔门票多少钱？", [])
        assert result == "大雁塔门票多少钱？"
        assert not called  # 确认未调用LLM


class TestRewriteWithLLM:
    """有指代词时的LLM改写"""

    def test_successful_rewrite(self, monkeypatch):
        """LLM正常返回 → 使用改写结果"""
        monkeypatch.setattr(
            query_rewrite, "chat_sync",
            lambda *a, **kw: "大雁塔几点关门？"
        )
        history = [
            {"role": "user", "content": "大雁塔的历史？"},
            {"role": "assistant", "content": "大雁塔建于唐代..."},
            {"role": "user", "content": "它几点关门？"},
        ]
        result = query_rewrite.rewrite("它几点关门？", history)
        assert result == "大雁塔几点关门？"

    def test_empty_fallback_to_original(self, monkeypatch):
        """LLM返回空字符串 → 回退原问题"""
        monkeypatch.setattr(
            query_rewrite, "chat_sync",
            lambda *a, **kw: ""
        )
        result = query_rewrite.rewrite("它怎么样", [])
        assert result == "它怎么样"

    def test_whitespace_fallback(self, monkeypatch):
        """LLM返回纯空白 → 回退原问题"""
        monkeypatch.setattr(
            query_rewrite, "chat_sync",
            lambda *a, **kw: "   \n\t  "
        )
        result = query_rewrite.rewrite("这个好吗", [])
        assert result == "这个好吗"


class TestRewriteErrorHandling:
    """异常容错测试"""

    def test_llm_exception_fallback(self, monkeypatch):
        """LLM调用异常 → 回退原问题，不抛异常"""
        def raise_error(*a, **kw):
            raise ConnectionError("API超时")

        monkeypatch.setattr(query_rewrite, "chat_sync", raise_error)
        result = query_rewrite.rewrite("那家店在哪", [])
        assert result == "那家店在哪"  # 正常返回，不崩溃

    def test_network_timeout_fallback(self, monkeypatch):
        """网络超时 → 降级为原问题"""
        def timeout(*a, **kw):
            raise TimeoutError("连接超时")

        monkeypatch.setattr(query_rewrite, "chat_sync", timeout)
        result = query_rewrite.rewrite("刚才说的那个", [])
        assert "刚才说的那个" in result


class TestHistoryWindow:
    """历史消息窗口限制测试"""

    def test_history_truncated_to_6_messages(self, monkeypatch):
        """只传最近6条消息（3轮）给LLM"""
        captured_history = []
        
        def capture_and_return(messages, **kw):
            captured_history.append(messages)
            return "改写后的问题"

        monkeypatch.setattr(query_rewrite, "chat_sync", capture_and_return)

        history = [
            {"role": "user", "content": f"第{i}轮问题"} for i in range(10)
        ]
        query_rewrite.rewrite("它呢", history)

        sent_messages = captured_history[0]
        user_content = sent_messages[1]["content"]
        assert user_content.count("\\n") <= 5  # 最多6条消息=5个换行


class TestPronounCoverage:
    """指代词覆盖率测试（确保所有预设词都被覆盖）"""

    @pytest.mark.parametrize("pronoun", [
        "它", "他", "她", "这个", "那个", "这家", "那家",
        "这里", "那里", "刚才", "之前", "上面的", "还有", "呢", "怎么样",
    ])
    def test_all_pronouns_trigger_rewrite(self, pronoun):
        assert query_rewrite.needs_rewrite(f"{pronoun}好不好？") is True