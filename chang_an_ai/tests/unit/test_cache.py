"""Agent 工具缓存单元测试（ToolCache + cached_tool 装饰器）。

覆盖：
- 命中/未命中统计与命中率计算
- TTL 过期淘汰
- LRU 容量淘汰
- get_or_compute 幂等
- cached_tool 装饰器：命中不执行原函数、error/falsy 结果不写缓存
"""
from __future__ import annotations

import json
import pytest

from app.agent.cache import ToolCache, cached_tool, _make_cache_key


class TestToolCache:
    """ToolCache 基础行为与命中率统计。"""

    def test_first_get_misses(self):
        cache = ToolCache(ttl=30.0)
        assert cache.get("k") is None
        assert cache.get_stats()["hits"] == 0
        assert cache.get_stats()["misses"] == 1

    def test_second_get_hits(self):
        cache = ToolCache(ttl=30.0)
        cache.set("k", "v")
        assert cache.get("k") == "v"
        assert cache.get("k") == "v"
        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 0
        assert stats["hit_rate"] == "100.0%"

    def test_hit_rate_statistics(self):
        """miss, hit, hit → 66.7%。"""
        cache = ToolCache(ttl=30.0)
        cache.get("k")            # miss
        cache.set("k", "v")
        cache.get("k")            # hit
        cache.get("k")            # hit
        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "66.7%"

    def test_ttl_expiry(self, monkeypatch):
        """TTL 到期后同 key 再次 get 应 miss 且缓存被删除。"""
        fake_time = {"now": 1000.0}
        monkeypatch.setattr("app.agent.cache.time.time", lambda: fake_time["now"])

        cache = ToolCache(ttl=5.0)
        cache.set("k", "v")
        assert cache.get("k") == "v"

        fake_time["now"] = 1010.0  # 超过 TTL 5s

        assert cache.get("k") is None
        assert cache.get_stats()["misses"] == 1
        assert cache.get_stats()["size"] == 0

    def test_lru_eviction_when_full(self):
        """容量满时写入新 key 应淘汰最旧条目。"""
        cache = ToolCache(ttl=30.0, max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # 淘汰最旧的 a
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get_stats()["size"] == 2

    def test_get_or_compute_only_computes_on_miss(self):
        cache = ToolCache(ttl=30.0)
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return "computed"

        assert cache.get_or_compute("k", compute) == "computed"
        assert cache.get_or_compute("k", compute) == "computed"
        assert calls["n"] == 1  # 第二次命中，不执行 compute


class TestMakeCacheKey:
    def test_same_args_same_key(self):
        assert _make_cache_key("t", {"a": 1, "b": 2}) == _make_cache_key("t", {"b": 2, "a": 1})

    def test_different_args_different_key(self):
        assert _make_cache_key("t", {"a": 1}) != _make_cache_key("t", {"a": 2})

    def test_tool_name_affects_key(self):
        assert _make_cache_key("t1", {"a": 1}) != _make_cache_key("t2", {"a": 1})


class TestCachedToolDecorator:
    """cached_tool 装饰器的命中率与写缓存策略。"""

    def test_miss_then_hit_skips_function(self):
        cache = ToolCache(ttl=30.0)
        calls = {"n": 0}

        @cached_tool(cache, "fake_tool")
        def fake(x: int) -> str:
            calls["n"] += 1
            return json.dumps({"value": x})

        assert json.loads(fake(x=1))["value"] == 1   # miss → 执行
        assert json.loads(fake(x=1))["value"] == 1   # hit → 跳过执行
        assert calls["n"] == 1
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "50.0%"

    def test_hit_rate_after_repeated_calls(self):
        """10 次相同参数调用 → 90% 命中率（1 miss + 9 hits）。"""
        cache = ToolCache(ttl=30.0)

        @cached_tool(cache, "fake_tool")
        def fake(x: int) -> str:
            return json.dumps({"value": x})

        for _ in range(10):
            fake(x=1)
        stats = cache.get_stats()
        assert stats["hits"] == 9
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "90.0%"

    def test_different_args_are_separate_entries(self):
        cache = ToolCache(ttl=30.0)
        calls = {"n": 0}

        @cached_tool(cache, "fake_tool")
        def fake(x: int) -> str:
            calls["n"] += 1
            return json.dumps({"value": x})

        fake(x=1)
        fake(x=2)
        fake(x=1)  # 命中第一次的结果
        assert calls["n"] == 2

    def test_error_result_not_cached(self):
        """返回 error JSON 的结果不写缓存（每次重算）。"""
        cache = ToolCache(ttl=30.0)
        calls = {"n": 0}

        @cached_tool(cache, "fake_tool")
        def fake(x: int) -> str:
            calls["n"] += 1
            return json.dumps({"error": "查询失败"})

        fake(x=1)
        fake(x=1)
        assert calls["n"] == 2
        assert cache.get_stats()["size"] == 0

    def test_falsy_result_not_cached(self):
        cache = ToolCache(ttl=30.0)
        calls = {"n": 0}

        @cached_tool(cache, "fake_tool")
        def fake(x: int) -> str:
            calls["n"] += 1
            return ""

        fake(x=1)
        fake(x=1)
        assert calls["n"] == 2
        assert cache.get_stats()["size"] == 0
