"""意图特征提取器：从用户输入中提取多维意图特征。

提取维度：
1. 时间意图：实时（现在/今天）vs 历史（去年/以前）vs 未定义
2. 精确度意图：精确（准确/具体）vs 大概（大约/左右）vs 未定义
3. 数据类型：价格/评价/介绍/运营信息
4. 主体类型：景点/店铺/美食/活动

设计原则：
- 纯字符串操作，<1ms延迟
- 词库外部化（见vocabularies.py），便于扩展
- 支持多关键词匹配
- 配置与逻辑分离
"""

import logging
from typing import List, Optional

from app.intent.models import (
    IntentFeatures,
    TimeIntent,
    PrecisionIntent,
    DataType,
    SubjectType,
)
from app.intent.vocabularies import get_vocabularies

logger = logging.getLogger(__name__)


class IntentFeatureExtractor:
    """从用户输入中提取意图特征。

    词库已外部化到 vocabularies.py，本类只负责：
    1. 特征提取逻辑
    2. 关键词匹配算法
    3. 结果封装
    """

    def __init__(self, vocabularies=None):
        """初始化特征提取器。

        Args:
            vocabularies: 词库实例（可选，用于测试时注入Mock对象）
        """
        self.vocab = vocabularies or get_vocabularies()

    def extract(self, message: str) -> IntentFeatures:
        """从用户输入中提取所有意图特征。

        Args:
            message: 用户原始输入

        Returns:
            IntentFeatures: 提取的多维特征
        """
        features = IntentFeatures(raw_message=message)

        # 提取各维度特征
        features.time_intent = self._extract_time_intent(message)
        features.precision_intent = self._extract_precision_intent(message)
        features.data_type = self._extract_data_type(message)
        features.subject_type = self._extract_subject_type(message)
        features.matched_keywords = self._extract_all_keywords(message)

        logger.debug(f"特征提取结果: {features}")
        return features

    def _extract_time_intent(self, message: str) -> TimeIntent:
        """提取时间维度意图。"""
        time_realtime, time_historical = self.vocab.get_time_vocabs()

        if any(word in message for word in time_realtime):
            return TimeIntent.REALTIME
        elif any(word in message for word in time_historical):
            return TimeIntent.HISTORICAL
        return TimeIntent.UNDEFINED

    def _extract_precision_intent(self, message: str) -> PrecisionIntent:
        """提取精确度维度意图。"""
        precision_exact, precision_approx = self.vocab.get_precision_vocabs()

        if any(word in message for word in precision_exact):
            return PrecisionIntent.EXACT
        elif any(word in message for word in precision_approx):
            return PrecisionIntent.APPROXIMATE
        return PrecisionIntent.UNDEFINED

    def _extract_data_type(self, message: str) -> DataType:
        """提取数据类型维度。"""
        data_pricing, data_rating, data_info, data_operation = (
            self.vocab.get_data_type_vocabs()
        )

        if any(word in message for word in data_pricing):
            return DataType.PRICING
        elif any(word in message for word in data_rating):
            return DataType.RATING
        elif any(word in message for word in data_operation):
            return DataType.OPERATION
        elif any(word in message for word in data_info):
            return DataType.INFO
        return DataType.UNDEFINED

    def _extract_subject_type(self, message: str) -> SubjectType:
        """提取主体类型维度。"""
        subject_attraction, subject_shop, subject_food, subject_activity = (
            self.vocab.get_subject_type_vocabs()
        )

        if any(word in message for word in subject_attraction):
            return SubjectType.ATTRACTION
        elif any(word in message for word in subject_shop):
            return SubjectType.SHOP
        elif any(word in message for word in subject_food):
            return SubjectType.FOOD
        elif any(word in message for word in subject_activity):
            return SubjectType.ACTIVITY
        return SubjectType.UNDEFINED

    def _extract_all_keywords(self, message: str) -> List[str]:
        """提取所有匹配到的关键词（用于调试）。"""
        keywords = []
        all_vocabs = self.vocab.get_all_vocabs()

        for category, word_set in all_vocabs.items():
            for word in word_set:
                if word in message:
                    keywords.append(f"{category}:{word}")

        return keywords


# 单例实例（全局复用）
_extractor_instance: Optional[IntentFeatureExtractor] = None


def get_feature_extractor() -> IntentFeatureExtractor:
    """获取特征提取器单例。"""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = IntentFeatureExtractor()
    return _extractor_instance