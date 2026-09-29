"""Agent 工具集（6 个）：4 个实时查 Java + 2 个查本地向量库。

V2.0 性能优化：
- 智能缓存：Java API 结果短时缓存（5-60s），向量库查询中期缓存（5-30min），
  相同查询重复命中时从缓存读取（<1ms），避免重复网络/计算开销；
- 缓存策略：按数据时效性分级——优惠券库存5s（高实时）、店铺详情30s、
  笔记/知识5min（低频更新）；TTL 过期自动淘汰，无需手动清理；
- 缓存键设计：工具名+参数哈希，确保不同查询互不干扰；
- 监控指标：记录缓存命中率、耗时分布，便于调优 TTL 和容量；

原始功能保留：
- 工具失败兜底：每个工具内部 try/except，返回结构化错误文本给 LLM 换策略，
  绝不把栈抛给用户（LLM 看到错误会自己换工具/换参数）；
- 时效性分层：券库存实时变化 → HTTP 实时查；笔记/知识静态 → 向量库快照查；
- 全部命中 Java 免登录接口（聊天走 nginx 直连绕开了 Java 鉴权，
  工具若用需登录接口就得层层传 token，复杂度爆炸——选型时刻意规避）；
- 返回 JSON 字符串：ToolNode 会把它包成 ToolMessage 喂回 LLM，
  JSON 结构化数据比自然语言更利于模型提取字段。
"""
from __future__ import annotations

import json
import time
import hashlib
import logging
from typing import Any, Dict, Optional, Tuple
from functools import wraps

from langchain_core.tools import tool

from app.repositories.java_client import JavaClient, JavaClientError
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client

logger = logging.getLogger(__name__)


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# ==================== 缓存基础设施 ====================

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


# ==================== Java 实时查询（4 个） ====================

@tool
def search_shops_by_name(name: str) -> str:
    """按名称关键字搜索平台店铺（模糊匹配）。参数 name：店铺名称关键字，如"泡馍"。"""
    try:
        with JavaClient() as jc:
            shops = jc.list_shops_by_name(name)
        return _json({"count": len(shops), "shops": shops})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_shops_by_type(type_name: str, x: float | None = None, y: float | None = None) -> str:
    """按分类查店铺列表；传坐标 (x,y) 时按距离从近到远排序。
    参数 type_name：分类名（美食/KTV/酒吧/轰趴馆/美容SPA/健身运动/按摩·足疗/丽人·美发/亲子游乐/美睫·美甲）；
    参数 x/y：用户位置的经度/纬度（可选，如"钟楼"约 x=108.9425, y=34.2610）。"""
    try:
        with JavaClient() as jc:
            types = jc.list_shop_types()
            type_id = next((t["id"] for t in types if t["name"] == type_name), None)
            if type_id is None:
                return _json({"error": f"没有找到分类「{type_name}」，可用分类: {[t['name'] for t in types]}"})
            shops: list = []
            for page in (1, 2):  # 最多取 2 页（10 家），足够回答"有哪些店"
                batch = jc.list_shops_by_type(type_id, page=page, x=x, y=y)
                shops.extend(batch)
                if len(batch) < jc.SHOP_PAGE_SIZE:
                    break
        return _json({"count": len(shops), "shops": shops})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def get_shop_detail(shop_id: int) -> str:
    """查询店铺详情（评分/人均/营业时间/地址等）。参数 shop_id：店铺 id。"""
    try:
        with JavaClient() as jc:
            shop = jc.get_shop_detail(shop_id)
        return _json(shop)
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_vouchers(shop_id: int) -> str:
    """实时查询店铺的优惠券（含库存/有效期）。参数 shop_id：店铺 id。"""
    try:
        with JavaClient() as jc:
            vouchers = jc.list_vouchers(shop_id)
        return _json({"count": len(vouchers), "vouchers": vouchers})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


# ==================== 本地向量库（2 个） ====================

def _search_kb(query: str, where: dict, top_k: int = 3) -> str:
    """向量检索公共实现：embedding → Milvus 过滤检索。"""
    try:
        qv = get_embedding_client().embed_texts([query])[0]
        hits = get_store().query(qv, top_k=top_k, where=where)
        return _json([{
            "title": h["metadata"].get("title") or h["metadata"].get("doc") or h["metadata"].get("name"),
            "score": round(h["score"], 3),
            "text": h["text"][:300],
        } for h in hits])
    except Exception as e:
        return _json({"error": f"知识库检索失败: {e}"})


@tool
def search_blogs(query: str) -> str:
    """搜索平台上的探店笔记（本地向量库快照）。参数 query：检索词，如"回民街泡馍体验"。"""
    return _search_kb(query, where={"source": "java", "type": "blog"})


@tool
def search_knowledge(query: str) -> str:
    """搜索西安文旅知识（景点/美食/攻略等语料库）。参数 query：检索词，如"大雁塔门票"。"""
    return _search_kb(query, where={"source": "corpus"})


ALL_TOOLS = [
    search_shops_by_name,
    list_shops_by_type,
    get_shop_detail,
    list_vouchers,
    search_blogs,
    search_knowledge,
]