"""query_rewrite 测试：两程流水线（关键词→LLM兜底）+ 改写校验 + 容错机制。

核心验证：
1. 关键词命中 → 直接触发改写（不经过 LLM 兜底）
2. 关键词未命中 → LLM 兜底分类（仅 ~15% 的 query 经过）
3. 复合词（其他/其它/其实等）→ 关键词不命中，依赖 LLM 兜底
4. 改写结果校验：空串/纯标点 → 回退原句
5. LLM 分类/改写异常 → 回退原句（永不阻塞）
"""
import pytest
from app.services import query_rewrite


class TestNeedsRewriteKeywords:
    """needs_rewrite 关键词快速通路（第一程）—— 有明确指代词时直接返回 True"""

    def test_pronoun_it(self):
        assert query_rewrite.needs_rewrite("它几点关门？") is True

    def test_pronoun_he(self):
        assert query_rewrite.needs_rewrite("他推荐的餐厅") is True

    def test_pronoun_she(self):
        assert query_rewrite.needs_rewrite("她说的那个地方") is True

    def test_pronoun_this_store(self):
        assert query_rewrite.needs_rewrite("这家店怎么样？") is True

    def test_pronoun_that_store(self):
        assert query_rewrite.needs_rewrite("那家店在哪") is True

    def test_pronoun_here(self):
        assert query_rewrite.needs_rewrite("这里有什么好吃的？") is True

    def test_pronoun_there(self):
        assert query_rewrite.needs_rewrite("那里几点开门") is True

    def test_pronoun_this_side(self):
        assert query_rewrite.needs_rewrite("这边有什么推荐") is True

    def test_pronoun_that_side(self):
        assert query_rewrite.needs_rewrite("那边过去远吗") is True

    def test_pronoun_here_colloq(self):
        assert query_rewrite.needs_rewrite("这儿附近有面馆吗") is True

    def test_pronoun_there_colloq(self):
        assert query_rewrite.needs_rewrite("那儿几点关门") is True

    def test_pronoun_these(self):
        assert query_rewrite.needs_rewrite("这些景点哪个值得去") is True

    def test_pronoun_those(self):
        assert query_rewrite.needs_rewrite("那些店还有优惠吗") is True

    def test_pronoun_this_one(self):
        assert query_rewrite.needs_rewrite("这个好吃吗") is True

    def test_pronoun_that_one(self):
        assert query_rewrite.needs_rewrite("那个多少钱") is True

    def test_pronoun_this_shop_formal(self):
        assert query_rewrite.needs_rewrite("该店的招牌菜是什么") is True

    def test_discourse_just_now(self):
        assert query_rewrite.needs_rewrite("刚才说的那个景点") is True

    def test_discourse_before(self):
        assert query_rewrite.needs_rewrite("之前提到的餐厅") is True

    def test_discourse_above(self):
        assert query_rewrite.needs_rewrite("上面的推荐还有吗") is True

    def test_real_pronoun_after_compound(self):
        """复合词抹掉后仍有真实指代：'其实它很好吃' → 应触发"""
        assert query_rewrite.needs_rewrite("其实它很好吃") is True


class TestNeedsRewriteLLMFallback:
    """needs_rewrite LLM 兜底（第二程）—— 关键词未命中时由 LLM 分类判断"""

    @pytest.fixture(autouse=True)
    def _mock_llm_classify_false(self, monkeypatch):
        """默认 mock _llm_classify 返回 False，测试关键词过滤能力"""
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: False)

    def test_no_pronoun(self):
        assert query_rewrite.needs_rewrite("大雁塔的历史？") is False

    def test_empty_question(self):
        assert query_rewrite.needs_rewrite("") is False

    def test_general_question(self):
        assert query_rewrite.needs_rewrite("西安三日游怎么安排？") is False

    def test_compound_qita(self):
        """'其他'包含'他'，但关键词过滤后不命中"""
        assert query_rewrite.needs_rewrite("其他的问题") is False

    def test_compound_qita2(self):
        """'其它'包含'它'，但关键词过滤后不命中"""
        assert query_rewrite.needs_rewrite("其它的还有什么") is False

    def test_compound_qizhong(self):
        assert query_rewrite.needs_rewrite("其中哪个最好吃") is False

    def test_compound_qishi(self):
        assert query_rewrite.needs_rewrite("其实我还没去过") is False

    def test_compound_youqi(self):
        assert query_rewrite.needs_rewrite("尤其是回民街") is False

    def test_compound_taren(self):
        assert query_rewrite.needs_rewrite("他人的评价怎么样") is False

    def test_compound_taxiang(self):
        assert query_rewrite.needs_rewrite("他乡的美食") is False

    def test_removed_ne(self):
        assert query_rewrite.needs_rewrite("大雁塔在哪呢") is False

    def test_removed_zenmeyang(self):
        assert query_rewrite.needs_rewrite("西安怎么样") is False

    def test_removed_haiyou(self):
        assert query_rewrite.needs_rewrite("西安还有哪些景点") is False


class TestLLMClassify:
    """_llm_classify 单元测试"""

    def test_classify_returns_true_on_yes(self, monkeypatch):
        """LLM 返回 'yes' → True"""
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "yes")
        assert query_rewrite._llm_classify("几点关门") is True

    def test_classify_returns_true_on_yes_capital(self, monkeypatch):
        """LLM 返回 'Yes' → True（大小写不敏感）"""
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "Yes!")
        assert query_rewrite._llm_classify("那个在哪") is True

    def test_classify_returns_false_on_no(self, monkeypatch):
        """LLM 返回 'no' → False"""
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "no")
        assert query_rewrite._llm_classify("大雁塔在哪") is False

    def test_classify_handles_exception(self, monkeypatch):
        """LLM 异常 → False（安全兜底）"""
        def raise_err(*a, **kw):
            raise ConnectionError("超时")
        monkeypatch.setattr(query_rewrite, "chat_sync", raise_err)
        result = query_rewrite._llm_classify("哪个好吃")
        assert result is False  # 不抛异常，静默降级

    def test_classify_handles_empty_response(self, monkeypatch):
        """LLM 返回空 → False"""
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "")
        assert query_rewrite._llm_classify("地址在哪") is False


class TestNeedsRewriteLLMIntegration:
    """集成测试：关键词未命中 → LLM 兜底触发改写"""

    def test_llm_fallback_triggers_rewrite(self, monkeypatch):
        """关键词不命中，但 LLM 分类返回 True → needs_rewrite 返回 True"""
        original_classify = query_rewrite._llm_classify
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: True)
        # "几点关门" 没有关键词匹配，走 LLM 兜底
        assert query_rewrite.needs_rewrite("几点关门") is True

    def test_omitted_subject_detected_by_llm(self, monkeypatch):
        """省略主语（'在哪里'）→ 关键词不命中，LLM 应识别"""
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: True)
        assert query_rewrite.needs_rewrite("在哪里") is True

    def test_keyword_hit_shortcuts_before_llm(self, monkeypatch):
        """关键词命中 → 不调 _llm_classify"""
        called = []
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: called.append(1) or True)
        result = query_rewrite.needs_rewrite("这家店在哪")
        assert result is True
        assert not called  # 关键词先命中了，LLM 兜底未执行


class TestRewriteShortCircuit:
    """needs_rewrite 返回 False 时的短路优化（不调用改写 LLM）"""

    @pytest.fixture(autouse=True)
    def _mock_llm_classify_false(self, monkeypatch):
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: False)

    def test_no_rewrite_needed(self, monkeypatch):
        """关键词+LLM 均不命中 → 原样返回，不调 chat_sync 做改写"""
        called = []
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: called.append(1) or "改写后")
        result = query_rewrite.rewrite("大雁塔门票多少钱？", [])
        assert result == "大雁塔门票多少钱？"
        assert not called

    def test_compound_no_rewrite(self, monkeypatch):
        """复合词不命中关键词 + LLM 兜底返回 False → 不触发改写"""
        called = []
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: called.append(1) or "改写后")
        result = query_rewrite.rewrite("其他的问题", [])
        assert result == "其他的问题"
        assert not called

    def test_removed_word_no_rewrite(self, monkeypatch):
        called = []
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: called.append(1) or "改写后")
        result = query_rewrite.rewrite("西安怎么样", [])
        assert result == "西安怎么样"
        assert not called


class TestRewriteWithLLM:
    """有指代词时的 LLM 改写（关键词命中路径）"""

    def test_successful_rewrite(self, monkeypatch):
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
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "")
        result = query_rewrite.rewrite("它怎么样", [])
        assert result == "它怎么样"

    def test_whitespace_fallback(self, monkeypatch):
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "   \n\t  ")
        result = query_rewrite.rewrite("这个好吗", [])
        assert result == "这个好吗"

    def test_punctuation_only_fallback(self, monkeypatch):
        monkeypatch.setattr(query_rewrite, "chat_sync", lambda *a, **kw: "？")
        result = query_rewrite.rewrite("她推荐的", [])
        assert result == "她推荐的"


class TestRewriteErrorHandling:
    """异常容错测试"""

    def test_llm_exception_fallback(self, monkeypatch):
        def raise_error(*a, **kw):
            raise ConnectionError("API超时")
        monkeypatch.setattr(query_rewrite, "chat_sync", raise_error)
        result = query_rewrite.rewrite("那家店在哪", [])
        assert result == "那家店在哪"

    def test_network_timeout_fallback(self, monkeypatch):
        def timeout(*a, **kw):
            raise TimeoutError("连接超时")
        monkeypatch.setattr(query_rewrite, "chat_sync", timeout)
        result = query_rewrite.rewrite("刚才说的那个", [])
        assert "刚才说的那个" in result


class TestHistoryWindow:
    """历史消息窗口限制测试"""

    def test_history_truncated_to_6_messages(self, monkeypatch):
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
        for i in range(4, 10):
            assert f"第{i}轮" in user_content, f"第{i}轮应在历史中"
        assert "第3轮" not in user_content, "第3轮应被截断"
        assert "第0轮" not in user_content, "第0轮应被截断"


class TestPronounCoverage:
    """指代词覆盖率测试 + 复合词黑名单"""

    @pytest.fixture(autouse=True)
    def _mock_llm_classify_false(self, monkeypatch):
        """复合词测试需要 mock _llm_classify 避免真实 LLM 调用"""
        monkeypatch.setattr(query_rewrite, "_llm_classify", lambda q: False)

    @pytest.mark.parametrize("pronoun", [
        "这家", "那家", "该店",
        "这里", "那里", "这边", "那边", "这儿", "那儿",
        "这个", "那个", "这些", "那些",
        "它", "他", "她",
        "刚才", "之前", "上面的",
    ])
    def test_all_pronouns_trigger_rewrite(self, pronoun):
        assert query_rewrite.needs_rewrite(f"{pronoun}好不好？") is True

    @pytest.mark.parametrize("compound", [
        "其他", "其它", "其中", "其实", "尤其", "他人", "他乡",
    ])
    def test_compounds_not_trigger(self, compound):
        assert query_rewrite.needs_rewrite(f"{compound}的问题") is False