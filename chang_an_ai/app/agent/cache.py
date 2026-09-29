"""Agent 工具缓存模块（TTL + LRU 淘汰策略）。

设计要点：
- 分级缓存：按数据时效性配置不同 TTL（5s/30s/5min）；
- 自动过期：TTL 到期自动淘汰，避免脏数据；
- 容量限制：LRU 淘汰最旧条目，防内存泄漏；
- 统计监控：记录命中率/耗时分布，便于调优；
- 线程安全：CPython 中 dict + time.time() 为原子操作。

使用方式：
    from app.agent.cache import CachedToolsWrapper, voucher_cache, shop_cache, vector_cache

    # 方式1：使用包装器（推荐）
    wrapper = CachedToolsWrapper(tools)
    cached_tools = wrapper.get_cached_tools()

    # 方式2：直接使用缓存实例
    result = shop_cache.get_or_compute(key, lambda: expensive_operation())
"""
from __future__ import annotations

import json
import time
import hashlib
import logging
from typing import Any, Dict, Optional, Tuple
from functools import wraps

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


class ToolCache:
    """工具结果缓存器（TTL + 容量限制）。

    设计要点：
    - 按 TTL 自动过期，避免脏数据；
    - 最大容量限制（LRU 淘汰），防内存泄漏；
    - 线程安全（dict + time.time() 在 CPython 中原子操作）；
    - 统计信息：命中率、调用次数，便于监控调优。

    使用方式：
        cache = ToolCache(ttl=30.0, max_size=1000)
        result = cache.get_or_compute(key, lambda: expensive_operation())
    """

    def __init__(self, ttl: float = 30.0, max_size: int = 1000):
        """
        Args:
            ttl: 缓存有效期（秒）
            max_size: 最大缓存条目数（超出则淘汰最旧的）
        """
        self.ttl = ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[float, Any]] = {}  # key → (timestamp, value)
        self._stats = {"hits": 0, "misses": 0}

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值（如果未过期）。

        Args:
            key: 缓存键

        Returns:
            缓存的值，如果不存在或已过期则返回 None
        """
        if key in self._cache:
            timestamp, value = self._cache[key]
            if time.time() - timestamp < self.ttl:
                self._stats["hits"] += 1
                logger.debug(f"[Cache] 命中: {key[:50]}...")
                return value
            else:
                # 已过期：删除
                del self._cache[key]

        self._stats["misses"] += 1
        return None

    def set(self, key: str, value: Any) -> None:
        """写入缓存。

        如果超过最大容量，先淘汰最旧的条目。

        Args:
            key: 缓存键
            value: 缓存值
        """
        # 容量检查：如果已满，删除最旧的条目
        if len(self._cache) >= self.max_size and key not in self._cache:
            oldest_key = min(self._cache.keys(), k=lambda k: self._cache[k][0])
            del self._cache[oldest_key]
            logger.debug(f"[Cache] 淘汰最旧条目: {oldest_key[:50]}...")

        self._cache[key] = (time.time(), value)
        logger.debug(f"[Cache] 写入: {key[:50]}...")

    def get_or_compute(self, key: str, compute_fn, *args, **kwargs) -> Any:
        """获取缓存或计算值。

        Args:
            key: 缓存键
            compute_fn: 缓存未命中时的计算函数
            *args, **kwargs: 传递给 compute_fn 的参数

        Returns:
            缓存或新计算的值
        """
        value = self.get(key)
        if value is not None:
            return value

        value = compute_fn(*args, **kwargs)
        self.set(key, value)
        return value

    def clear(self) -> None:
        """清空缓存。"""
        self._cache.clear()
        logger.info("[Cache] 缓存已清空")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息。

        Returns:
            包含 hits, misses, hit_rate, size 的字典
        """
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            **self._stats,
            "hit_rate": f"{hit_rate:.1f}%",
            "size": len(self._cache),
            "ttl": self.ttl,
            "max_size": self.max_size,
        }


def _make_cache_key(tool_name: str, args: dict, kwargs: dict = None) -> str:
    """生成缓存键（工具名 + 参数哈希）。

    Args:
        tool_name: 工具名称
        args: 位置参数字典
        kwargs: 关键字参数字典（可选）

    Returns:
        SHA1 哈希字符串（40位）
    """
    key_data = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
    if kwargs:
        key_data += f":{json.dumps(kwargs, sort_keys=True)}"
    return hashlib.sha1(key_data.encode("utf-8")).hexdigest()


# ==================== 分级缓存实例 ====================

# L1: 高实时数据缓存（优惠券库存 - 5秒）
voucher_cache = ToolCache(ttl=5.0, max_size=500)

# L2: 中实时数据缓存（店铺详情/列表 - 30秒）
shop_cache = ToolCache(ttl=30.0, max_size=1000)

# L3: 低频变化数据缓存（向量库查询 - 5分钟）
vector_cache = ToolCache(ttl=300.0, max_size=500)


# ==================== 缓存装饰器 ====================

def cached_tool(cache: ToolCache, tool_name: str):
    """工具缓存装饰器。

    自动为工具函数添加缓存能力：
    - 缓存键：工具名 + 参数哈希
    - 缓存未命中：执行原函数并写入缓存
    - 异常处理：原函数异常时不写入缓存

    Args:
        cache: ToolCache 实例
        tool_name: 工具名称（用于生成缓存键）

    Returns:
        装饰后的函数
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = _make_cache_key(tool_name, args[0] if args else {}, kwargs)

            # 尝试从缓存获取
            result = cache.get(cache_key)
            if result is not None:
                return result

            # 缓存未命中：执行原函数
            result = func(*args, **kwargs)

            # 写入缓存（仅成功结果）
            if result and not json.loads(result).get("error"):
                cache.set(cache_key, result)

            return result

        return wrapper
    return decorator


class CachedToolsWrapper:
    """带缓存的工具包装器。

    为所有工具自动添加缓存能力，对外暴露与原始工具相同的接口。

    使用方式：
        cached_tools = CachedToolsWrapper(ALL_TOOLS)
        tools_with_cache = cached_tools.get_cached_tools()
    """

    def __init__(self, tools: list):
        """
        Args:
            tools: 原始工具列表
        """
        self.original_tools = tools
        self._cached_tools = self._wrap_with_cache(tools)

    def _wrap_with_cache(self, tools: list) -> list:
        """为每个工具选择合适的缓存策略并包装。

        缓存策略映射：
        - list_vouchers → voucher_cache (5s)
        - get_shop_detail / search_shops_by_name / list_shops_by_type → shop_cache (30s)
        - search_blogs / search_knowledge → vector_cache (5min)

        Args:
            tools: 原始工具列表

        Returns:
            带缓存的工具列表
        """
        cached = []
        for tool_func in tools:
            tool_name = tool_func.name

            # 根据工具名称选择缓存策略
            if tool_name == "list_vouchers":
                wrapped = cached_tool(voucher_cache, tool_name)(tool_func.func)
            elif tool_name in ("get_shop_detail", "search_shops_by_name", "list_shops_by_type"):
                wrapped = cached_tool(shop_cache, tool_name)(tool_func.func)
            elif tool_name in ("search_blogs", "search_knowledge"):
                wrapped = cached_tool(vector_cache, tool_name)(tool_func.func)
            else:
                # 默认：不缓存
                wrapped = tool_func.func

            # 重新创建工具对象（保持元数据）
            cached_tool_obj = tool(wrapped)
            cached_tool_obj.name = tool_name
            cached_tool_obj.description = tool_func.description
            cached_tool_obj.args_schema = tool_func.args_schema
            cached.append(cached_tool_obj)

            logger.info(f"[CachedTools] 工具 {tool_name} 已配置缓存: "
                       f"{self._get_cache_ttl(tool_name)}s")

        return cached

    def _get_cache_ttl(self, tool_name: str) -> float:
        """获取工具的缓存TTL。

        Args:
            tool_name: 工具名称

        Returns:
            TTL（秒）
        """
        cache_map = {
            "list_vouchers": voucher_cache.ttl,
            "get_shop_detail": shop_cache.ttl,
            "search_shops_by_name": shop_cache.ttl,
            "list_shops_by_type": shop_cache.ttl,
            "search_blogs": vector_cache.ttl,
            "search_knowledge": vector_cache.ttl,
        }
        return cache_map.get(tool_name, 0)

    def get_cached_tools(self) -> list:
        """获取带缓存的工具列表。

        Returns:
            缓存包装后的工具列表
        """
        return self._cached_tools

    def get_all_cache_stats(self) -> Dict[str, Dict]:
        """获取所有缓存的统计信息。

        Returns:
            工具名 → 缓存统计的字典
        """
        stats = {}
        for tool_func in self.original_tools:
            tool_name = tool_func.name
            if tool_name == "list_vouchers":
                stats[tool_name] = voucher_cache.get_stats()
            elif tool_name in ("get_shop_detail", "search_shops_by_name", "list_shops_by_type"):
                stats[tool_name] = shop_cache.get_stats()
            elif tool_name in ("search_blogs", "search_knowledge"):
                stats[tool_name] = vector_cache.get_stats()
        return stats

    def clear_all_caches(self) -> None:
        """清空所有缓存。"""
        voucher_cache.clear()
        shop_cache.clear()
        vector_cache.clear()
        logger.info("[CachedTools] 所有工具缓存已清空")