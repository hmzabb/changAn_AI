"""工具调用参数校验守卫（第二层防御）：防止LLM传入非法参数。

设计要点：
- 参数类型校验 → 类型错误直接拒绝本次调用
- 参数值域白名单 → 不在合法范围直接拒绝
- 空参数/缺省检测 → 返回引导信息让LLM修正
- 防重复调用 → 相同参数短时间重复调用直接用缓存
- 坐标范围校验 → 防止查询其他城市

使用方式：
    from app.agent.tool_guard import ToolGuard
    
    # 在工具函数中调用
    error = ToolGuard.validate_shop_type(type_name)
    if error:
        return _json({"error": error})
"""
from __future__ import annotations
import json
import time
import hashlib
import logging
from typing import Any, Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

# ==================== 白名单配置 ====================

# 有效的分类名白名单（从Java后端获取的实际值）
VALID_SHOP_TYPES: set[str] = {
    "美食", "KTV", "酒吧", "轰趴馆", "美容SPA",
    "健身运动", "按摩·足疗", "丽人·美发", "亲子游乐", "美睫·美甲"
}

# 西安大致坐标范围（防止查询其他城市）
XI_AN_BOUNDS: Dict[str, float] = {
    "x_min": 108.7,
    "x_max": 109.2,
    "y_min": 34.1,
    "y_max": 34.5,
}

# 合理的店铺ID范围（根据你的系统实际ID范围调整）
VALID_SHOP_ID_RANGE: Tuple[int, int] = (1, 99999)

# 常见商圈坐标映射（用于提示LLM使用正确坐标）
COMMON_LOCATIONS: Dict[str, Tuple[float, float]] = {
    "钟楼": (108.9425, 34.2610),
    "回民街": (108.9403, 34.2622),
    "小寨": (108.9488, 34.2303),
    "大雁塔": (108.9644, 34.2186),
    "大唐不夜城": (108.9644, 34.2186),
    "城墙": (108.9472, 34.2597),
    "兵马俑": (109.2852, 34.3847),
    "华清宫": (109.2146, 34.3639),
}


class ToolGuard:
    """工具调用参数校验器。

    策略：
    1. 参数类型校验 → 类型错误直接拒绝本次调用
    2. 参数值域白名单 → 不在合法范围直接拒绝
    3. 空参数/缺省检测 → 返回引导信息让LLM修正
    4. 防重复调用 → 相同参数短时间重复调用直接用缓存
    5. 坐标范围校验 → 防止查询其他城市
    """

    # 最近调用记录：{(tool_name, args_hash): timestamp}
    _recent_calls: Dict[str, float] = {}
    _min_call_interval: float = 2.0  # 同一调用至少间隔2秒

    @classmethod
    def validate_shop_type(cls, type_name: str) -> Optional[str]:
        """验证分类名是否合法。

        Args:
            type_name: 分类名字符串

        Returns:
            None 表示通过；返回错误信息字符串表示不通过
        """
        if not type_name or not isinstance(type_name, str):
            return f"❌ 分类名不能为空，请从 {VALID_SHOP_TYPES} 中选择"

        if type_name not in VALID_SHOP_TYPES:
            # 模糊匹配：给出最接近的建议
            suggestions = cls._fuzzy_match_type(type_name)
            if suggestions:
                return (
                    f"❌ 分类名「{type_name}」不合法，您是否想查「{suggestions[0]}」？\n"
                    f"   可用分类: {VALID_SHOP_TYPES}"
                )
            return f"❌ 分类名「{type_name}」不在可用列表中\n   可用分类: {VALID_SHOP_TYPES}"

        return None

    @classmethod
    def validate_coordinates(
        cls,
        x: Optional[float],
        y: Optional[float]
    ) -> Optional[str]:
        """验证坐标是否在西安范围内。

        Args:
            x: 经度
            y: 纬度

        Returns:
            None 表示通过；返回错误信息字符串表示不通过
        """
        if x is None and y is None:
            return None  # 坐标可选，不传也行

        if x is not None:
            try:
                x_float = float(x)
                if x_float < XI_AN_BOUNDS["x_min"] or x_float > XI_AN_BOUNDS["x_max"]:
                    return (
                        f"❌ 经度 {x} 不在西安范围({XI_AN_BOUNDS['x_min']}~{XI_AN_BOUNDS['x_max']})\n"
                        f"   请使用西安坐标或常见商圈名（钟楼/回民街/小寨/大雁塔等）\n"
                        f"   示例：钟楼约 x=108.9425, y=34.2610"
                    )
            except (ValueError, TypeError):
                return f"❌ 经度 {x} 不是有效数字"

        if y is not None:
            try:
                y_float = float(y)
                if y_float < XI_AN_BOUNDS["y_min"] or y_float > XI_AN_BOUNDS["y_max"]:
                    return (
                        f"❌ 纬度 {y} 不在西安范围({XI_AN_BOUNDS['y_min']}~{XI_AN_BOUNDS['y_max']})\n"
                        f"   请使用西安坐标或常见商圈名（钟楼/回民街/小寨/大雁塔等）\n"
                        f"   示例：钟楼约 x=108.9425, y=34.2610"
                    )
            except (ValueError, TypeError):
                return f"❌ 纬度 {y} 不是有效数字"

        return None

    @classmethod
    def validate_shop_id(cls, shop_id: Any) -> Optional[str]:
        """验证店铺ID是否在合理范围内。

        Args:
            shop_id: 店铺ID

        Returns:
            None 表示通过；返回错误信息字符串表示不通过
        """
        if shop_id is None:
            return "❌ 店铺ID不能为空，必须从 list_shops_by_type 或 search_shops_by_name 的返回结果中获取"

        try:
            shop_id_int = int(shop_id)
            min_id, max_id = VALID_SHOP_ID_RANGE
            if shop_id_int < min_id or shop_id_int > max_id:
                return (
                    f"❌ 店铺ID {shop_id} 不在合理范围({min_id}~{max_id})\n"
                    f"   ⚠️ 不要自己编造shop_id！必须先调 list_shops_by_type 或 search_shops_by_name 获取真实ID"
                )
        except (ValueError, TypeError):
            return f"❌ 店铺ID {shop_id} 不是有效整数"

        return None

    @classmethod
    def validate_search_query(cls, query: str) -> Optional[str]:
        """验证搜索关键词是否合理。

        Args:
            query: 搜索关键词

        Returns:
            None 表示通过；返回错误信息字符串表示不通过
        """
        if not query or not isinstance(query, str) or not query.strip():
            return "❌ 搜索关键词不能为空，请提供具体的搜索内容"

        if len(query.strip()) < 1:
            return "❌ 搜索关键词太短，请提供更具体的内容"

        if len(query) > 100:
            return "❌ 搜索关键词过长(>100字符)，请精简搜索内容"

        return None

    @classmethod
    def check_duplicate_call(
        cls,
        tool_name: str,
        args: dict,
        interval: Optional[float] = None
    ) -> Optional[str]:
        """检测短时间内重复调用。

        Args:
            tool_name: 工具名称
            args: 工具参数字典
            interval: 最小调用间隔（秒），默认使用类属性 _min_call_interval

        Returns:
            None 表示可以调用；返回错误信息字符串表示应拒绝
        """
        check_interval = interval or cls._min_call_interval
        key_data = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        key = hashlib.md5(key_data.encode("utf-8")).hexdigest()
        now = time.time()

        if key in cls._recent_calls:
            elapsed = now - cls._recent_calls[key]
            if elapsed < check_interval:
                logger.warning(
                    f"[ToolGuard] 检测到重复调用: {tool_name}, "
                    f"间隔仅 {elapsed:.1f}s (< {check_interval}s)"
                )
                return (
                    f"⏰ 相同参数的工具「{tool_name}」在 {elapsed:.1f}秒前刚执行过\n"
                    f"   请勿重复调用相同参数！换一种策略或直接基于已有结果回答用户。"
                )

        cls._recent_calls[key] = now
        return None

    @classmethod
    def clear_duplicate_cache(cls) -> None:
        """清空重复调用缓存（测试用）。"""
        cls._recent_calls.clear()
        logger.info("[ToolGuard] 重复调用缓存已清空")

    @staticmethod
    def _fuzzy_match_type(type_name: str) -> List[str]:
        """模糊匹配分类名，返回最接近的建议。

        Args:
            type_name: 用户输入的分类名

        Returns:
            匹配到的建议列表（最多3个）
        """
        matches = []
        for valid_type in VALID_SHOP_TYPES:
            # 完全包含关系
            if type_name in valid_type or valid_type in type_name:
                matches.append(valid_type)
            # 首字匹配
            elif type_name[0] == valid_type[0]:
                matches.append(valid_type)

        return matches[:3]

    @classmethod
    def get_location_coordinates(cls, location_name: str) -> Optional[Tuple[float, float]]:
        """根据地名获取坐标。

        Args:
            location_name: 地名（如"钟楼"、"回民街"）

        Returns:
            (x, y) 坐标元组，未找到返回None
        """
        return COMMON_LOCATIONS.get(location_name)

    @classmethod
    def get_validation_stats(cls) -> Dict[str, Any]:
        """获取校验统计信息（监控用）。

        Returns:
            包含统计信息的字典
        """
        return {
            "recent_calls_count": len(cls._recent_calls),
            "valid_shop_types": len(VALID_SHOP_TYPES),
            "xi_an_bounds": XI_AN_BOUNDS,
            "common_locations": list(COMMON_LOCATIONS.keys()),
        }