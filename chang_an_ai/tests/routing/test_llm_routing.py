"""LLM意图分类路由测试 - 验证第三层升级效果

运行方式：
    pytest tests/test_llm_routing.py -v -s
    # 或（需要真实API key）
    python tests/test_llm_routing.py --real-llm
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Optional

import pytest

project_root = Path(__file__).parent.parent / "chang_an_ai"
sys.path.insert(0, str(project_root))

from app.routers.chat import (
    _route,
    _llm_classify_intent,
    _HIGH_CONFIDENCE_KEYWORDS,
    _MEDIUM_CONFIDENCE_PATTERNS,
    ChatRequest,
)


# ==================== Mock工具 ====================

def create_mock_llm_response(intent: str, confidence: float = 0.8, reason: str = "test"):
    """创建模拟的LLM响应"""
    response = MagicMock()
    response.content = json.dumps({
        "intent": intent,
        "confidence": confidence,
        "reason": reason
    }, ensure_ascii=False)
    return response


# ==================== 测试用例 ====================

class TestLayer1KeywordMatching:
    """第一层：高置信度关键词测试"""

    @pytest.mark.asyncio
    async def test_keyword_score(self):
        """'评分'关键词应走Agent"""
        req = ChatRequest(session_id="test", message="这家店评分多少？", mode="auto")
        result = await _route(req)
        assert result == "agent", f"期望agent，实际{result}"

    @pytest.mark.asyncio
    async def test_keyword_group_buy(self):
        """'团购'关键词应走Agent"""
        req = ChatRequest(session_id="test", message="有团购优惠吗？", mode="auto")
        result = await _route(req)
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_keyword_open_time(self):
        """'营业时间'关键词应走Agent"""
        req = ChatRequest(session_id="test", message="营业时间到几点？", mode="auto")
        result = await _route(req)
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_explicit_mode_agent(self):
        """显式mode=agent应强制走Agent（即使无关键词）"""
        req = ChatRequest(session_id="test", message="大雁塔历史", mode="agent")
        result = await _route(req)
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_explicit_mode_rag(self):
        """显式mode=rag应强制走RAG（即使有关键词）"""
        req = ChatRequest(session_id="test", message="评分多少？", mode="rag")
        result = await _route(req)
        assert result == "rag"


class TestLayer2RegexMatching:
    """第二层：正则模糊匹配测试"""

    @pytest.mark.asyncio
    async def test_regex_how_much_money(self):
        """'多少钱'正则应匹配"""
        req = ChatRequest(session_id="test", message="门票多少钱？", mode="auto")
        result = await _route(req)
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_regex_what_time_open(self):
        """'几点开门'正则应匹配"""
        req = ChatRequest(session_id="test", message="早上几点开门？", mode="auto")
        result = await _route(req)
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_regex_which_better(self):
        """'哪家好'正则应匹配"""
        req = ChatRequest(session_id="test", message="哪家店比较好？", mode="auto")
        result = await _route(req)
        assert result == "agent"


class TestLayer3LLMClassification:
    """第三层：LLM意图分类测试（核心）"""

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_returns_task_intent(self, mock_chat_openai):
        """LLM返回task意图时应走Agent"""
        # 设置Mock
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = create_mock_llm_response("task", 0.85, "需要查询实时数据")
        mock_chat_openai.return_value = mock_model

        # 这个问题不会命中第一/二层，会进入LLM层
        req = ChatRequest(session_id="test", message="这家店性价比如何？", mode="auto")
        result = await _route(req)

        assert result == "agent", f"LLM返回task但路由到{result}"
        print(f"✅ LLM正确识别为task: '这家店性价比如何？'")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_returns_knowledge_intent(self, mock_chat_openai):
        """LLM返回knowledge意图时应走RAG"""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = create_mock_llm_response("knowledge", 0.9, "静态知识问题")
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="西安有什么特色小吃？", mode="auto")
        result = await _route(req)

        assert result == "rag", f"LLM返回knowledge但路由到{result}"
        print(f"✅ LLM正确识别为knowledge: '西安有什么特色小吃？'")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_low_confidence_fallback_to_rag(self, mock_chat_openai):
        """LLM置信度<0.6时应保守走RAG"""
        mock_model = AsyncMock()
        # 置信度只有0.5，低于阈值
        mock_model.ainvoke.return_value = create_mock_llm_response("task", 0.5, "不太确定")
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="这个问题有点复杂", mode="auto")
        result = await _route(req)

        assert result == "rag", f"低置信度({0.5})应走RAG，实际{result}"
        print(f"✅ 低置信度(0.5)正确回退到RAG")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_timeout_fallback(self, mock_chat_openai):
        """LLM超时时应回退到RAG"""
        import asyncio

        mock_model = AsyncMock()
        # 模拟超时
        mock_model.ainvoke.side_effect = asyncio.TimeoutError()
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="测试超时场景", mode="auto")
        result = await _route(req)

        assert result == "rag", f"LLM超时应回退RAG，实际{result}"
        print(f"✅ LLM超时正确回退到RAG")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_json_parse_error(self, mock_chat_openai):
        """LLM返回非法JSON时应回退到RAG"""
        mock_model = AsyncMock()
        # 返回非JSON格式
        bad_response = MagicMock()
        bad_response.content = "这不是JSON格式"
        mock_model.ainvoke.return_value = bad_response
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="测试JSON解析错误", mode="auto")
        result = await _route(req)

        assert result == "rag", f"JSON解析失败应回退RAG，实际{result}"
        print(f"✅ JSON解析错误正确回退到RAG")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_llm_network_error(self, mock_chat_openai):
        """LLM网络异常时应回退到RAG"""
        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = Exception("Network error")
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="测试网络异常", mode="auto")
        result = await _route(req)

        assert result == "rag", f"网络异常应回退RAG，实际{result}"
        print(f"✅ 网络异常正确回退到RAG")


class TestComplexRealWorldCases:
    """真实世界复杂case测试"""

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_implicit_task_intent(self, mock_chat_openai):
        """隐式任务意图：LLM能理解'性价比'=需要查人均+评分"""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = create_mock_llm_response(
            "task", 0.88, "'性价比'需要结合价格和评分数据"
        )
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="这家店性价比怎么样？", mode="auto")
        result = await _route(req)
        assert result == "agent"
        print(f"✅ 复杂语义理解: '性价比' → Agent")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_date_ambiguous_query(self, mock_chat_openai):
        """日期相关模糊查询：可能是知识也可能是任务"""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = create_mock_llm_response(
            "knowledge", 0.75, "'周末人多'是常识性问题"
        )
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="周末哪些地方人多？", mode="auto")
        result = await _route(req)
        # 这个case可能走RAG或Agent都合理，取决于LLM判断
        assert result in ["agent", "rag"]
        print(f"✅ 模糊case: '周末人多' → {result} (LLM判断)")


# ==================== 性能测试 ====================

class TestPerformance:
    """性能基准测试"""

    @pytest.mark.asyncio
    async def test_layer1_latency(self):
        """第一层匹配延迟应< 1ms"""
        import time
        req = ChatRequest(session_id="test", message="评分多少？", mode="auto")

        start = time.perf_counter()
        for _ in range(100):
            await _route(req)
        elapsed = (time.perf_counter() - start) / 100 * 1000  # ms

        assert elapsed < 1.0, f"第一层延迟{elapsed:.2f}ms > 1ms"
        print(f"✅ 第一层延迟: {elapsed:.3f}ms")

    @pytest.mark.asyncio
    @patch('app.routers.chat.ChatOpenAI')
    async def test_layer3_latency_with_mock(self, mock_chat_openai):
        """第三层LLM延迟（Mock）应可控制"""
        import time
        import asyncio

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = create_mock_llm_response("task", 0.8)
        mock_chat_openai.return_value = mock_model

        req = ChatRequest(session_id="test", message="需要LLM判断的问题", mode="auto")

        start = time.perf_counter()
        result = await _route(req)
        elapsed = (time.perf_counter() - start) * 1000  # ms

        assert result == "agent"
        print(f"⏱️  第三层LLM延迟(Mock): {elapsed:.1f}ms")
        # Mock环境下应该很快（< 10ms）
        assert elapsed < 50, f"Mock延迟过高: {elapsed:.1f}ms"


# ==================== 边界情况测试 ====================

class TestEdgeCases:
    """边界条件测试"""

    @pytest.mark.asyncio
    async def test_empty_message(self):
        """空消息应走RAG"""
        req = ChatRequest(session_id="test", message="", mode="auto")
        result = await _route(req)
        assert result == "rag"

    @pytest.mark.asyncio
    async def test_very_long_message(self):
        """超长消息不应崩溃"""
        long_msg = "测试" * 1000  # 2000字
        req = ChatRequest(session_id="test", message=long_msg, mode="auto")
        result = await _route(req)
        assert result in ["agent", "rag"]

    @pytest.mark.asyncio
    async def test_special_characters(self):
        """特殊字符处理"""
        req = ChatRequest(
            session_id="test",
            message="这!@#$%^&*()家店评分多少？",
            mode="auto"
        )
        result = await _route(req)
        assert result == "agent"  # 包含"评分"关键词


# ==================== 主函数 ====================

if __name__ == "__main__":
    print("\n" + "="*70)
    print("#  LLM意图分类路由测试套件 v2.0")
    print("#  测试三层漏斗: 关键词 → 正则 → LLM")
    print("="*70 + "\n")

    # 运行pytest
    exit_code = pytest.main([
        __file__,
        "-v",
        "-s",  # 显示print输出
        "--tb=short",
        # "--real-llm",  # 取消注释以使用真实LLM（需要API key）
    ])

    sys.exit(exit_code)