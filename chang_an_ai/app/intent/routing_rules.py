"""路由规则配置表。

设计原则：
- 规则与引擎分离，便于独立维护
- 支持动态加载（未来可从数据库/配置中心读取）
- 规则优先级通过列表顺序控制
"""

from typing import Dict, List, Any

from app.intent.models import (
    TimeIntent,
    PrecisionIntent,
    DataType,
    SubjectType,
)


class RoutingRules:
    """路由规则集中管理。

    规则结构：
    {
        "name": "规则名称",
        "conditions": {
            "time_intent": [TimeIntent.REALTIME],
            "data_type": [DataType.PRICING],
        },
        "action": "agent" | "rag",
        "confidence": 0.95,
        "reason": "决策理由"
    }
    """

    RULES: List[Dict[str, Any]] = [
        # =====================================================================
        # 优先级1: 实时查询 + 动态数据 → Agent（最高优先级）
        # =====================================================================
        {
            "name": "realtime_dynamic_query",
            "conditions": {
                "time_intent": [TimeIntent.REALTIME],
                "data_type": [
                    DataType.PRICING,
                    DataType.OPERATION,
                    DataType.RATING,
                ],
            },
            "action": "agent",
            "confidence": 0.95,
            "reason": "实时数据查询需求（价格/运营/评价）",
        },

        # =====================================================================
        # 优先级2: 参考性查询 + 价格/介绍 → RAG
        # =====================================================================
        {
            "name": "reference_query",
            "conditions": {
                "precision_intent": [PrecisionIntent.APPROXIMATE],
                "data_type": [
                    DataType.PRICING,
                    DataType.INFO,
                    DataType.RATING,
                ],
            },
            "action": "rag",
            "confidence": 0.92,
            "reason": "参考性知识查询（大概/大约类问题）",
        },

        # =====================================================================
        # 优先级3: 店铺主体 + 动态数据 → Agent
        # =====================================================================
        {
            "name": "shop_dynamic_data",
            "conditions": {
                "subject_type": [SubjectType.SHOP],
                "data_type": [
                    DataType.PRICING,
                    DataType.RATING,
                    DataType.OPERATION,
                ],
            },
            "action": "agent",
            "confidence": 0.98,
            "reason": "店铺动态数据查询（必须实时查库）",
        },

        # =====================================================================
        # 优先级4: 景点主体 + 介绍/文化/历史 → RAG
        # =====================================================================
        {
            "name": "attraction_knowledge",
            "conditions": {
                "subject_type": [SubjectType.ATTRACTION],
                "data_type": [DataType.INFO],
            },
            "action": "rag",
            "confidence": 0.97,
            "reason": "景点知识性内容（静态知识库强项）",
        },

        # =====================================================================
        # 优先级5: 精确查询 + 价格 → RAG（官方定价是静态的）
        # =====================================================================
        {
            "name": "exact_pricing",
            "conditions": {
                "precision_intent": [PrecisionIntent.EXACT],
                "data_type": [DataType.PRICING],
            },
            "action": "rag",
            "confidence": 0.90,
            "reason": "精确价格查询（官方定价/标准价）",
        },

        # =====================================================================
        # 优先级6: 美食/活动主体 → RAG（知识库内容丰富）
        # =====================================================================
        {
            "name": "food_activity_knowledge",
            "conditions": {
                "subject_type": [SubjectType.FOOD, SubjectType.ACTIVITY],
                "data_type": [DataType.INFO, DataType.RATING],
            },
            "action": "rag",
            "confidence": 0.88,
            "reason": "美食/活动知识性内容",
        },

        # =====================================================================
        # 优先级7: 历史查询 → RAG（历史数据不变）
        # =====================================================================
        {
            "name": "historical_query",
            "conditions": {
                "time_intent": [TimeIntent.HISTORICAL],
            },
            "action": "rag",
            "confidence": 0.93,
            "reason": "历史数据查询（静态知识）",
        },

        # =====================================================================
        # 优先级8: 默认规则 → LLM分类兜底
        # =====================================================================
        {
            "name": "default_to_llm",
            "conditions": {},
            "action": "llm_fallback",
            "confidence": 0.50,
            "reason": "无明确规则匹配，交由LLM判断",
        },
    ]

    @classmethod
    def get_rules(cls) -> List[Dict[str, Any]]:
        """获取所有路由规则。"""
        return cls.RULES

    @classmethod
    def get_rule_by_name(cls, name: str) -> Dict[str, Any]:
        """根据名称获取规则（用于测试和调试）。"""
        for rule in cls.RULES:
            if rule["name"] == name:
                return rule
        return None


# 全局单例
_rules_instance: RoutingRules = None


def get_routing_rules() -> RoutingRules:
    """获取路由规则单例。"""
    global _rules_instance
    if _rules_instance is None:
        _rules_instance = RoutingRules()
    return _rules_instance