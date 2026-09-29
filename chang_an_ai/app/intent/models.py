"""意图特征和决策的数据模型。"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class TimeIntent(str, Enum):
    """时间意图枚举。"""
    REALTIME = "realtime"       # 现在/今天/实时
    HISTORICAL = "historical"   # 去年/历史/以前
    UNDEFINED = "undefined"     # 未明确指定


class PrecisionIntent(str, Enum):
    """精确度意图枚举。"""
    EXACT = "exact"             # 精确/准确/具体
    APPROXIMATE = "approximate" # 大概/大约/左右
    UNDEFINED = "undefined"     # 未明确指定


class DataType(str, Enum):
    """数据类型枚举。"""
    PRICING = "pricing"         # 价格相关
    RATING = "rating"           # 评价相关
    INFO = "info"               # 介绍/攻略类
    OPERATION = "operation"     # 营业时间/电话等
    UNDEFINED = "undefined"     # 未明确指定


class SubjectType(str, Enum):
    """主体类型枚举。"""
    ATTRACTION = "attraction"   # 景点/博物馆
    SHOP = "shop"               # 店铺/餐厅
    FOOD = "food"               # 美食/小吃
    ACTIVITY = "activity"       # 活动/演出
    UNDEFINED = "undefined"     # 未明确指定


@dataclass
class IntentFeatures:
    """用户输入的意图特征。

    Attributes:
        time_intent: 时间维度意图（实时/历史/未定义）
        precision_intent: 精确度维度意图（精确/大概/未定义）
        data_type: 数据类型（价格/评价/介绍/运营）
        subject_type: 主体类型（景点/店铺/美食/活动）
        matched_keywords: 匹配到的所有关键词列表
        raw_message: 原始用户输入
    """
    time_intent: TimeIntent = TimeIntent.UNDEFINED
    precision_intent: PrecisionIntent = PrecisionIntent.UNDEFINED
    data_type: DataType = DataType.UNDEFINED
    subject_type: SubjectType = SubjectType.UNDEFINED
    matched_keywords: List[str] = field(default_factory=list)
    raw_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于日志和调试）。"""
        return {
            "time_intent": self.time_intent.value,
            "precision_intent": self.precision_intent.value,
            "data_type": self.data_type.value,
            "subject_type": self.subject_type.value,
            "matched_keywords": self.matched_keywords,
            "raw_message": self.raw_message[:50] + "..." if len(self.raw_message) > 50 else self.raw_message,
        }

    def __str__(self) -> str:
        return (f"IntentFeatures(time={self.time_intent.value}, "
                f"precision={self.precision_intent.value}, "
                f"data={self.data_type.value}, "
                f"subject={self.subject_type.value})")


@dataclass
class RoutingDecision:
    """路由决策结果。

    Attributes:
        action: 路由动作（agent/rag/llm_fallback）
        confidence: 决策置信度（0.0-1.0）
        reason: 决策理由（可解释性）
        matched_rule: 匹配到的规则名称（如果有）
        features: 关联的意图特征（用于调试）
    """
    action: str  # "agent" | "rag" | "llm_fallback"
    confidence: float
    reason: str
    matched_rule: Optional[str] = None
    features: Optional[IntentFeatures] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        result = {
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_rule": self.matched_rule,
        }
        if self.features:
            result["features"] = self.features.to_dict()
        return result

    def __str__(self) -> str:
        return (f"RoutingDecision(action={self.action}, "
                f"confidence={self.confidence:.2f}, "
                f"reason={self.reason})")