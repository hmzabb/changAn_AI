"""意图特征提取词库配置。

设计原则：
- 纯数据定义，零业务逻辑
- 按维度分类，便于维护和扩展
- 支持热更新（未来可对接配置中心）
"""

from typing import Dict, List, Set, Tuple


class IntentVocabularies:
    """意图识别词库集中管理。

    所有词库按维度分类存储，便于：
    1. 独立维护（不改业务代码）
    2. 单元测试（Mock词库）
    3. 动态加载（从数据库/配置中心）
    """

    # =========================================================================
    # 时间维度词库
    # =========================================================================
    TIME_REALTIME: Set[str] = {
        "现在", "今天", "目前", "实时", "最新", "当前",
        "这会儿", "此刻", "马上", "立刻",
    }

    TIME_HISTORICAL: Set[str] = {
        "去年", "历史", "以前", "过去", "原来",
        "之前", "从前", "以往", "古时候",
    }

    # =========================================================================
    # 精确度维度词库
    # =========================================================================
    PRECISION_EXACT: Set[str] = {
        "精确", "准确", "具体", "标准价", "官方定价",
        "确切", "明确", "详细", "精准",
    }

    PRECISION_APPROXIMATE: Set[str] = {
        "大概", "大约", "左右", "估计", "预计",
        "差不多", "应该", "可能", "一般",
        "平均", "通常", "普遍",
    }

    # =========================================================================
    # 数据类型词库
    # =========================================================================
    DATA_PRICING: Set[str] = {
        "价格", "多少钱", "贵不贵", "便宜", "人均",
        "消费", "票价", "门票", "收费", "花费",
        "预算", "价位", "成本",
    }

    DATA_RATING: Set[str] = {
        "评分", "星级", "好评", "口碑", "怎么样",
        "值得去吗", "好不好玩", "推荐吗", "评价",
        "排行", "排名", "几星",
    }

    DATA_INFO: Set[str] = {
        "介绍", "攻略", "历史", "文化", "特色",
        "推荐", "景点", "博物馆", "故事", "由来",
        "背景", "含义", "意义", "习俗", "传统",
    }

    DATA_OPERATION: Set[str] = {
        "营业时间", "开门", "关门", "电话", "地址",
        "交通", "路线", "怎么去", "位置", "在哪里",
        "联系方式", "开放时间", "闭馆", "闭店",
    }

    # =========================================================================
    # 主体类型词库
    # =========================================================================
    SUBJECT_ATTRACTION: Set[str] = {
        "景点", "景区", "博物馆", "古迹", "遗址",
        "寺庙", "道观", "教堂", "公园", "广场",
        "塔", "楼", "墙", "陵", "宫",
    }

    SUBJECT_SHOP: Set[str] = {
        "店铺", "店", "餐厅", "馆子", "商家",
        "摊位", "超市", "商场", "便利店", "专卖店",
    }

    SUBJECT_FOOD: Set[str] = {
        "美食", "小吃", "特色菜", "菜品", "料理",
        "饮料", "甜点", "零食", "土特产",
    }

    SUBJECT_ACTIVITY: Set[str] = {
        "活动", "演出", "展览", "节庆", "庆典",
        "表演", "音乐会", "戏剧", "电影",
    }

    # =========================================================================
    # 便捷访问接口（按类别分组）
    # =========================================================================
    @classmethod
    def get_all_vocabs(cls) -> Dict[str, Set[str]]:
        """获取所有词库（用于调试和测试）。"""
        return {
            "time_realtime": cls.TIME_REALTIME,
            "time_historical": cls.TIME_HISTORICAL,
            "precision_exact": cls.PRECISION_EXACT,
            "precision_approximate": cls.PRECISION_APPROXIMATE,
            "data_pricing": cls.DATA_PRICING,
            "data_rating": cls.DATA_RATING,
            "data_info": cls.DATA_INFO,
            "data_operation": cls.DATA_OPERATION,
            "subject_attraction": cls.SUBJECT_ATTRACTION,
            "subject_shop": cls.SUBJECT_SHOP,
            "subject_food": cls.SUBJECT_FOOD,
            "subject_activity": cls.SUBJECT_ACTIVITY,
        }

    @classmethod
    def get_time_vocabs(cls) -> Tuple[Set[str], Set[str]]:
        """获取时间维度词库。"""
        return cls.TIME_REALTIME, cls.TIME_HISTORICAL

    @classmethod
    def get_precision_vocabs(cls) -> Tuple[Set[str], Set[str]]:
        """获取精确度维度词库。"""
        return cls.PRECISION_EXACT, cls.PRECISION_APPROXIMATE

    @classmethod
    def get_data_type_vocabs(cls) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
        """获取数据类型词库。"""
        return (
            cls.DATA_PRICING,
            cls.DATA_RATING,
            cls.DATA_INFO,
            cls.DATA_OPERATION,
        )

    @classmethod
    def get_subject_type_vocabs(cls) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
        """获取主体类型词库。"""
        return (
            cls.SUBJECT_ATTRACTION,
            cls.SUBJECT_SHOP,
            cls.SUBJECT_FOOD,
            cls.SUBJECT_ACTIVITY,
        )


# 全局单例（避免重复创建）
_vocab_instance: IntentVocabularies = None


def get_vocabularies() -> IntentVocabularies:
    """获取词库单例。"""
    global _vocab_instance
    if _vocab_instance is None:
        _vocab_instance = IntentVocabularies()
    return _vocab_instance