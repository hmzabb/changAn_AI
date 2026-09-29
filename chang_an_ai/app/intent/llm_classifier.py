"""V4.0 LLM意图分类器（兜底方案）。

当规则引擎无法高置信度决策时，使用LLM进行深度语义理解。

优化点：
1. 注入特征信息，提高LLM判断准确率
2. 结构化输出，便于解析
3. 超时控制，避免阻塞主流程
4. 降级策略，LLM失败时保守选择RAG
5. Prompt外部化（见prompt_templates.py），便于A/B测试
"""

import json
import asyncio
import logging
from typing import Optional

from app.intent.models import IntentFeatures, RoutingDecision
from app.intent.prompt_templates import get_intent_prompts
from app.config import settings

logger = logging.getLogger(__name__)


async def llm_classify_v4(
    message: str,
    features: Optional[IntentFeatures] = None,
    timeout: float = 2.0,
    confidence_threshold: float = 0.6,
) -> RoutingDecision:
    """使用LLM进行V4.0意图分类。

    Args:
        message: 用户原始输入
        features: 已提取的特征（可选，注入Prompt提高准确率）
        timeout: 超时时间（秒）
        confidence_threshold: 置信度阈值（低于此值选择RAG）

    Returns:
        RoutingDecision: 路由决策结果
    """
    try:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=0,
            timeout=timeout + 1,
            max_retries=1,
        )

        # 从配置获取Prompt模板
        prompts = get_intent_prompts()
        prompt_template = prompts.get_classification_prompt(version="v4")

        # 构建Prompt（注入特征信息）
        prompt = prompt_template.format(
            question=message,
            time_intent=features.time_intent.value if features else "undefined",
            precision_intent=features.precision_intent.value if features else "undefined",
            data_type=features.data_type.value if features else "undefined",
            subject_type=features.subject_type.value if features else "undefined",
        )

        logger.info(f"调用LLM意图分类V4: message='{message[:30]}...'")

        response = await asyncio.wait_for(
            model.ainvoke(prompt),
            timeout=timeout
        )

        result = json.loads(response.content.strip())
        intent = result.get("intent", "knowledge")
        confidence = result.get("confidence", 0.5)
        reason = result.get("reason", "")
        suggested_action = result.get("suggested_action", "rag")

        logger.info(
            f"LLM分类V4结果: intent={intent}, "
            f"confidence={confidence}, reason={reason[:50]}"
        )

        # 根据置信度和意图决定路由
        if intent == "task" and confidence >= confidence_threshold:
            action = "agent"
        else:
            action = "rag"

        return RoutingDecision(
            action=action,
            confidence=confidence,
            reason=f"LLM分类(V4): {reason}",
            matched_rule="llm_classifier_v4",
            features=features,
        )

    except json.JSONDecodeError as e:
        logger.warning(f"LLM返回JSON解析失败: {e}")
        return RoutingDecision(
            action="rag",
            confidence=0.3,
            reason=f"LLM JSON解析失败: {e}",
            features=features,
        )

    except asyncio.TimeoutError:
        logger.warning(f"LLM分类超时(>{timeout}s)")
        return RoutingDecision(
            action="rag",
            confidence=0.2,
            reason=f"LLM分类超时(>{timeout}s)，默认RAG",
            features=features,
        )

    except Exception as e:
        logger.error(f"LLM分类异常: {e}", exc_info=True)
        return RoutingDecision(
            action="rag",
            confidence=0.1,
            reason=f"LLM分类异常: {e}，默认RAG",
            features=features,
        )