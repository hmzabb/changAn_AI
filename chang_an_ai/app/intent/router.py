"""意图路由决策引擎：基于特征的智能路由规则匹配。

设计原则：
1. 规则优先级：按列表顺序匹配，先匹配先生效
2. 置信度量化：每条规则有置信度评分
3. 可解释性：每条决策都有明确的reason
4. 兜底机制：无规则匹配时降级到LLM分类
5. 规则外部化（见routing_rules.py），便于独立维护

规则示例：
{
    "name": "实时价格查询",
    "conditions": {
        "time_intent": ["realtime"],
        "data_type": ["pricing"],
    },
    "action": "agent",
    "confidence": 0.95,
    "reason": "用户需要实时价格数据"
}
"""

import logging
from typing import Any, Dict, List, Optional

from app.intent.models import (
    IntentFeatures,
    RoutingDecision,
)
from app.intent.routing_rules import get_routing_rules

logger = logging.getLogger(__name__)


class IntentRouter:
    """基于意图特征的路由决策引擎。

    规则已外部化到 routing_rules.py，本类只负责：
    1. 规则匹配逻辑
    2. 条件判断算法
    3. 决策结果封装
    """

    def __init__(self, rules=None):
        """初始化路由器。

        Args:
            rules: 规则实例（可选，用于测试时注入Mock对象）
        """
        self.rules = rules or get_routing_rules()

    def route(self, features: IntentFeatures) -> RoutingDecision:
        """根据意图特征做路由决策。

        Args:
            features: 提取的意图特征

        Returns:
            RoutingDecision: 路由决策结果
        """
        logger.info(f"开始路由决策: {features}")

        # 从规则配置获取规则表
        rules_list = self.rules.get_rules()

        # 遍历规则表，找到第一个匹配的规则
        for rule in rules_list:
            if self._match_conditions(features, rule["conditions"]):
                decision = RoutingDecision(
                    action=rule["action"],
                    confidence=rule["confidence"],
                    reason=rule["reason"],
                    matched_rule=rule["name"],
                    features=features,
                )

                logger.info(f"路由决策: {decision}")
                return decision

        # 理论上不应该走到这里（因为最后一条规则无条件匹配）
        logger.warning(f"未匹配任何规则，使用默认RAG")
        return RoutingDecision(
            action="rag",
            confidence=0.5,
            reason="异常：未匹配任何规则",
            features=features,
        )

    def _match_conditions(
        self,
        features: IntentFeatures,
        conditions: Dict[str, List[Any]]
    ) -> bool:
        """检查特征是否满足所有条件。

        Args:
            features: 意图特征
            conditions: 条件字典，key是特征名，value是允许的值列表

        Returns:
            bool: 是否所有条件都满足
        """
        for condition_key, allowed_values in conditions.items():
            if not allowed_values:
                continue

            actual_value = self._get_feature_value(features, condition_key)
            if actual_value not in allowed_values:
                return False

        return True

    def _get_feature_value(self, features: IntentFeatures, key: str) -> Any:
        """根据key获取特征值。"""
        mapping = {
            "time_intent": features.time_intent,
            "precision_intent": features.precision_intent,
            "data_type": features.data_type,
            "subject_type": features.subject_type,
        }
        return mapping.get(key)


# 单例实例
_router_instance: Optional[IntentRouter] = None


def get_intent_router() -> IntentRouter:
    """获取路由器单例。"""
    global _router_instance
    if _router_instance is None:
        _router_instance = IntentRouter()
    return _router_instance