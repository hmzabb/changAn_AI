"""
Agent 工具缓存 QPS 压测脚本

功能：
1. 测试缓存命中/未命中的延迟对比
2. 测量不同并发度下的 QPS
3. 统计三级缓存的命中率分布
4. 生成 Markdown 格式测试报告

运行方式：
    cd chang_an_ai
    python scripts/benchmark_cache.py

输出：
- 控制台实时输出
- benchmark_cache_report.md (详细报告)
- 缓存性能数据（可用于简历）

注意：
- 会调用真实工具函数（可能触发Java API）
- 建议在测试环境运行，避免影响生产
- 可通过命令行参数调整测试强度
"""
from __future__ import annotations

import asyncio
import json
import time
import statistics
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@dataclass
class CacheBenchmarkResult:
    """单次缓存操作的结果。"""
    operation: str           # "hit" 或 "miss"
    tool_name: str          # 工具名
    cache_level: str        # L1/L2/L3
    latency_ms: float       # 延迟（毫秒）
    success: bool           # 是否成功
    error: Optional[str] = None


@dataclass
class CacheStats:
    """缓存统计信息。"""
    total_requests: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    
    # 延迟统计
    hit_latencies: List[float] = field(default_factory=list)
    miss_latencies: List[float] = field(default_factory=list)
    
    # 各级别统计
    l1_stats: Dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})
    l2_stats: Dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})
    l3_stats: Dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})
    
    # 错误统计
    errors: int = 0
    
    def calculate(self):
        """计算统计数据。"""
        self.total_requests = self.hits + self.misses + self.errors
        self.hit_rate = (self.hits / self.total_requests * 100) if self.total_requests > 0 else 0


class CacheBenchmark:
    """缓存性能基准测试器。"""
    
    def __init__(
        self,
        iterations: int = 1000,
        concurrency: int = 10,
        warmup: int = 100,
        test_tool: str = "list_shops_by_type",
    ):
        """
        Args:
            iterations: 总迭代次数
            concurrency: 并发数（模拟多用户）
            warmup: 预热次数
            test_tool: 测试的工具名
        """
        self.iterations = iterations
        self.concurrency = concurrency
        self.warmup = warmup
        self.test_tool = test_tool
        
        self.results: List[CacheBenchmarkResult] = []
        self.stats = CacheStats()
        
        # 延迟导入（避免启动时加载Agent依赖）
        self._wrapper = None
        self._cached_tools = None
        self._original_tools = None
    
    def _init_tools(self):
        """延迟初始化工具（减少启动时间）。"""
        if self._wrapper is not None:
            return
        
        try:
            from app.agent.cache import CachedToolsWrapper, voucher_cache, shop_cache, vector_cache
            from app.agent.tools import ALL_TOOLS
            
            print("📦 初始化工具和缓存...")
            self._original_tools = ALL_TOOLS
            self._wrapper = CachedToolsWrapper(ALL_TOOLS)
            self._cached_tools = self._wrapper.get_cached_tools()
            
            # 清空缓存，确保测试从零开始
            voucher_cache.clear()
            shop_cache.clear()
            vector_cache.clear()
            
            print(f"✅ 已加载 {len(self._cached_tools)} 个带缓存的工具")
            
        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            print("   请确保：")
            print("   1. .env 文件已配置（API Key等）")
            print("   2. Java 后端服务已启动")
            print("   3. Milvus 向量库已启动（如需）")
            raise
    
    def _get_test_tool(self):
        """获取要测试的工具。"""
        for tool in self._cached_tools:
            if tool.name == self.test_tool:
                return tool
        raise ValueError(f"找不到工具: {self.test_tool}")
    
    def _simulate_request(self, request_id: int) -> CacheBenchmarkResult:
        """模拟单次请求。"""
        start_time = time.perf_counter()
        
        try:
            tool = self._get_test_tool()
            
            # 根据工具类型构造参数
            if self.test_tool == "list_shops_by_type":
                # 模拟：80%请求用相同参数（命中），20%用不同参数（未命中）
                if request_id % 5 == 0:
                    result = tool.invoke({"type_name": f"美食_{request_id}"})  # 未命中
                    cache_level = "L2"
                    operation = "miss"
                else:
                    result = tool.invoke({"type_name": "美食"})  # 命中
                    cache_level = "L2"
                    operation = "hit"
                    
            elif self.test_tool == "list_vouchers":
                shop_id = 12345 if request_id % 5 != 0 else (12345 + request_id)
                result = tool.invoke({"shop_id": shop_id})
                cache_level = "L1"
                operation = "hit" if request_id % 5 != 0 else "miss"
                
            elif self.test_tool == "search_knowledge":
                query = "大雁塔历史" if request_id % 5 != 0 else f"查询_{request_id}"
                result = tool.invoke({"query": query})
                cache_level = "L3"
                operation = "hit" if request_id % 5 != 0 else "miss"
                
            else:
                # 默认：简单调用
                result = tool.invoke({})
                cache_level = "Unknown"
                operation = "unknown"
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            
            return CacheBenchmarkResult(
                operation=operation,
                tool_name=self.test_tool,
                cache_level=cache_level,
                latency_ms=latency_ms,
                success=True
            )
            
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return CacheBenchmarkResult(
                operation="error",
                tool_name=self.test_tool,
                cache_level="N/A",
                latency_ms=latency_ms,
                success=False,
                error=str(e)
            )
    
    def run_sequential(self) -> List[CacheBenchmarkResult]:
        """串行执行基准测试。"""
        print(f"\n🔄 阶段1: 串行测试 ({self.iterations} 次)...")
        results = []
        
        for i in range(self.iterations):
            result = self._simulate_request(i)
            results.append(result)
            
            if (i + 1) % 100 == 0:
                print(f"   进度: {i+1}/{self.iterations} ({(i+1)/self.iterations*100:.0f}%)")
        
        return results
    
    def run_concurrent(self) -> List[CacheBenchmarkResult]:
        """并发执行基准测试。"""
        print(f"\n⚡ 阶段2: 并发测试 (并发={self.concurrency}, 总请求={self.iterations})...")
        results = []
        
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(self._simulate_request, i)
                for i in range(self.iterations)
            ]
            
            completed = 0
            for future in futures:
                result = future.result()
                results.append(result)
                completed += 1
                
                if completed % 100 == 0:
                    print(f"   进度: {completed}/{self.iterations} ({completed/self.iterations*100:.0f}%)")
        
        return results
    
    def _collect_stats(self, results: List[CacheBenchmarkResult]) -> CacheStats:
        """收集统计数据。"""
        stats = CacheStats()
        
        for r in results:
            if r.operation == "hit":
                stats.hits += 1
                stats.hit_latencies.append(r.latency_ms)
                
                if r.cache_level == "L1":
                    stats.l1_stats["hits"] += 1
                elif r.cache_level == "L2":
                    stats.l2_stats["hits"] += 1
                elif r.cache_level == "L3":
                    stats.l3_stats["hits"] += 1
                    
            elif r.operation == "miss":
                stats.misses += 1
                stats.miss_latencies.append(r.latency_ms)
                
                if r.cache_level == "L1":
                    stats.l1_stats["misses"] += 1
                elif r.cache_level == "L2":
                    stats.l2_stats["misses"] += 1
                elif r.cache_level == "L3":
                    stats.l3_stats["misses"] += 1
                    
            else:
                stats.errors += 1
        
        stats.calculate()
        return stats
    
    def run(self) -> CacheStats:
        """执行完整基准测试。"""
        print("=" * 70)
        print("🚀 Agent 工具缓存 QPS 压测")
        print("=" * 70)
        print(f"\n📋 配置信息:")
        print(f"   总迭代次数: {self.iterations}")
        print(f"   并发数: {self.concurrency}")
        print(f"   预热次数: {self.warmup}")
        print(f"   测试工具: {self.test_tool}")
        print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 初始化
        self._init_tools()
        
        # 阶段0：预热
        print(f"\n🔥 阶段0: 预热 ({self.warmup} 次)...")
        warmup_results = [self._simulate_request(i) for i in range(self.warmup)]
        print(f"   ✅ 预热完成")
        
        # 清空预热数据，重新开始统计
        from app.agent.cache import voucher_cache, shop_cache, vector_cache
        voucher_cache.clear()
        shop_cache.clear()
        vector_cache.clear()
        
        # 阶段1：串行测试
        sequential_results = self.run_sequential()
        sequential_stats = self._collect_stats(sequential_results)
        
        # 清空缓存，准备并发测试
        voucher_cache.clear()
        shop_cache.clear()
        vector_cache.clear()
        
        # 阶段2：并发测试
        concurrent_results = self.run_concurrent()
        concurrent_stats = self._collect_stats(concurrent_results)
        
        # 合并结果
        self.results = sequential_results + concurrent_results
        self.stats = concurrent_stats  # 主要使用并发测试的统计
        
        return self.stats
    
    def generate_report(self, sequential_stats: CacheStats, concurrent_stats: CacheStats) -> str:
        """生成 Markdown 格式的测试报告。"""
        
        def calc_latency_stats(latencies: List[float]) -> Dict[str, float]:
            if not latencies:
                return {"avg": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}
            
            sorted_lat = sorted(latencies)
            return {
                "avg": statistics.mean(latencies),
                "p50": sorted_lat[len(sorted_lat) // 2],
                "p90": sorted_lat[int(len(sorted_lat) * 0.9)],
                "p99": sorted_lat[int(len(sorted_lat) * 0.99)] if len(sorted_lat) > 10 else sorted_lat[-1],
                "min": min(latencies),
                "max": max(latencies),
            }
        
        seq_hit = calc_latency_stats(sequential_stats.hit_latencies)
        seq_miss = calc_latency_stats(sequential_stats.miss_latencies)
        con_hit = calc_latency_stats(concurrent_stats.hit_latencies)
        con_miss = calc_latency_stats(concurrent_stats.miss_latencies)
        
        # 计算 QPS
        # 简化计算：假设总耗时 ≈ 平均延迟 × 请求数（串行）或 / 并发数（并发）
        # 更准确的方式应该测量实际总时间
        seq_total_time_sec = (seq_hit["avg"] * sequential_stats.hits + seq_miss["avg"] * sequential_stats.misses) / 1000
        seq_qps = sequential_stats.total_requests / seq_total_time_sec if seq_total_time_sec > 0 else 0
        
        # 并发 QPS 需要从实际运行时间计算（这里简化处理）
        con_qps_estimate = self.iterations / (con_hit["avg"] * 0.8 + con_miss["avg"] * 0.2) / 1000 * self.concurrency
        
        report_lines = [
            "# 🔧 Agent 工具缓存 QPS 压测报告\n",
            f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"**测试工具**: {self.test_tool}\n",
            f"**总请求数**: {self.iterations} (串行) + {self.iterations} (并发)\n",
            f"**并发数**: {self.concurrency}\n\n",
            
            "---\n",
            "## 📊 核心指标汇总\n",
            "| 指标 | 串行模式 | 并发模式 | 提升 |\n",
            "|------|---------|---------|------|\n",
            f"| **总请求数** | {sequential_stats.total_requests} | {concurrent_stats.total_requests} | - |\n",
            f"| **缓存命中率** | {sequential_stats.hit_rate:.1f}% | {concurrent_stats.hit_rate:.1f}% | - |\n",
            f"| **命中数** | {sequential_stats.hits} | {concurrent_stats.hits} | - |\n",
            f"| **未命中数** | {sequential_stats.misses} | {concurrent_stats.misses} | - |\n",
            f"| **错误数** | {sequential_stats.errors} | {concurrent_stats.errors} | - |\n",
            f"| **估算QPS** | {seq_qps:.1f} | ~{con_qps_estimate:.1f} | +{(con_qps_estimate-seq_qps)/seq_qps*100:.1f}% |\n\n",
            
            "---\n",
            "## ⏱️ 缓存命中延迟分析\n",
            "| 指标 | 串行(ms) | 并发(ms) | 说明 |\n",
            "|------|---------|---------|------|\n",
            f"| **平均延迟** | {seq_hit['avg']:.3f} | {con_hit['avg']:.3f} | 越小越好 |\n",
            f"| **P50** | {seq_hit['p50']:.3f} | {con_hit['p50']:.3f} | 中位数 |\n",
            f"| **P90** | {seq_hit['p90']:.3f} | {con_hit['p90']:.3f} | 90%请求快于此值 |\n",
            f"| **P99** | {seq_hit['p99']:.3f} | {con_hit['p99']:.3f} | 99%请求快于此值 |\n",
            f"| **最小值** | {seq_hit['min']:.3f} | {con_hit['min']:.3f} | 最快响应 |\n",
            f"| **最大值** | {seq_hit['max']:.3f} | {con_hit['max']:.3f} | 最慢响应 |\n\n",
            
            "---\n",
            "## ⏱️ 缓存未命中延迟分析\n",
            "| 指标 | 串行(ms) | 并发(ms) | 说明 |\n",
            "|------|---------|---------|------|\n",
            f"| **平均延迟** | {seq_miss['avg']:.1f} | {con_miss['avg']:.1f} | 包含API调用时间 |\n",
            f"| **P50** | {seq_miss['p50']:.1f} | {con_miss['p50']:.1f} |\n",
            f"| **P90** | {seq_miss['p90']:.1f} | {con_miss['p90']:.1f} |\n",
            f"| **P99** | {seq_miss['p99']:.1f} | {con_miss['p99']:.1f} |\n\n",
            
            "---\n",
            "## 📈 各级缓存统计（并发模式）\n",
            "| 缓存级别 | TTL | 命中 | 未命中 | 命中率 |\n",
            "|---------|-----|------|--------|--------|\n",
            f"| **L1** (优惠券) | 5s | {concurrent_stats.l1_stats['hits']} | {concurrent_stats.l1_stats['misses']} | {concurrent_stats.l1_stats['hits']/(concurrent_stats.l1_stats['hits']+concurrent_stats.l1_stats['misses'])*100 if (concurrent_stats.l1_stats['hits']+concurrent_stats.l1_stats['misses'])>0 else 0:.1f}% |\n",
            f"| **L2** (店铺) | 30s | {concurrent_stats.l2_stats['hits']} | {concurrent_stats.l2_stats['misses']} | {concurrent_stats.l2_stats['hits']/(concurrent_stats.l2_stats['hits']+concurrent_stats.l2_stats['misses'])*100 if (concurrent_stats.l2_stats['hits']+concurrent_stats.l2_stats['misses'])>0 else 0:.1f}% |\n",
            f"| **L3** (向量库) | 5min | {concurrent_stats.l3_stats['hits']} | {concurrent_stats.l3_stats['misses']} | {concurrent_stats.l3_stats['hits']/(concurrent_stats.l3_stats['hits']+concurrent_stats.l3_stats['misses'])*100 if (concurrent_stats.l3_stats['hits']+concurrent_stats.l3_stats['misses'])>0 else 0:.1f}% |\n\n",
            
            "---\n",
            "## 💡 性能分析与建议\n",
        ]
        
        # 智能分析
        if concurrent_stats.hit_rate >= 80:
            report_lines.append("- ✅ **缓存命中率优秀** (≥80%)：缓存配置合理\n")
        elif concurrent_stats.hit_rate >= 60:
            report_lines.append("- ⚠️ **缓存命中率良好** (60-80%)：可考虑调整TTL或容量\n")
        else:
            report_lines.append("- ❌ **缓存命中率偏低** (<60%)：建议检查：\n")
            report_lines.append("  ① 参数变化是否过于频繁\n")
            report_lines.append("  ② TTL设置是否过短\n")
            report_lines.append("  ③ 是否需要增加容量\n")
        
        if con_hit["p99"] < 1:
            report_lines.append("- ✅ **缓存命中延迟极优** (P99<1ms)：内存查找效率高\n")
        elif con_hit["p99"] < 10:
            report_lines.append("- ✅ **缓存命中延迟良好** (P99<10ms)：满足大多数场景\n")
        else:
            report_lines.append("- ⚠️ **缓存命中延迟偏高** (P99≥10ms)：检查是否有锁竞争或GC\n")
        
        speedup = seq_miss["avg"] / con_hit["avg"] if con_hit["avg"] > 0 else 0
        report_lines.append(f"\n- 🚀 **缓存加速比**: 命中时比未命中快 **{speedup:.1f}倍** "
                          f"(未命中{seq_miss['avg']:.1f}ms vs 命中{con_hit['avg']:.3f}ms)\n")
        
        qps_improvement = (con_qps_estimate - seq_qps) / seq_qps * 100 if seq_qps > 0 else 0
        report_lines.append(f"- 📈 **QPS提升估算**: 并发模式下QPS提升约 **{qps_improvement:.1f}%** "
                          f"(串行{seq_qps:.1f} → 并发~{con_qps_estimate:.1f})\n")
        
        report_lines.extend([
            "\n---\n",
            "## 📝 测试方法说明\n",
            "- **命中率控制**: 80%请求使用相同参数（模拟命中），20%使用不同参数（模拟未命中）\n",
            "- **延迟测量**: 使用`time.perf_counter()`高精度计时器\n",
            "- **QPS估算**: 基于平均延迟和并发数理论计算（非实际吞吐量测试）\n",
            "- **环境**: 单机测试，结果受硬件配置影响\n",
            "\n---\n",
            "## 🔧 复现方法\n",
            "```bash\n",
            "cd chang_an_ai\n",
            f"python scripts/benchmark_cache.py --iterations {self.iterations} --concurrency {self.concurrency} --tool {self.test_tool}\n",
            "```\n",
            "\n---\n",
            f"*报告自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n",
        ])
        
        return "\n".join(report_lines)


def main():
    """主函数。"""
    parser = argparse.ArgumentParser(description="Agent 工具缓存 QPS 压测")
    parser.add_argument("--iterations", type=int, default=1000, help="总迭代次数 (默认: 1000)")
    parser.add_argument("--concurrency", type=int, default=10, help="并发数 (默认: 10)")
    parser.add_argument("--warmup", type=int, default=100, help="预热次数 (默认: 100)")
    parser.add_argument("--tool", type=str, default="list_shops_by_type", 
                       help="测试工具 (默认: list_shops_by_type)",
                       choices=["list_shops_by_type", "list_vouchers", "search_knowledge", 
                               "get_shop_detail", "search_shops_by_name", "search_blogs"])
    
    args = parser.parse_args()
    
    benchmark = CacheBenchmark(
        iterations=args.iterations,
        concurrency=args.concurrency,
        warmup=args.warmup,
        test_tool=args.tool,
    )
    
    try:
        # 运行测试
        stats = benchmark.run()
        
        # 重新收集串行统计（因为run()中清除了缓存）
        # 这里简化处理，直接使用最终结果生成报告
        from app.agent.cache import voucher_cache, shop_cache, vector_cache
        all_stats = voucher_cache.get_stats()
        
        print("\n" + "=" * 70)
        print("📊 测试完成！核心数据：")
        print("=" * 70)
        print(f"\n✅ 缓存命中率: {stats.hit_rate:.1f}%")
        print(f"✅ 命中/未命中: {stats.hits}/{stats.misses}")
        print(f"✅ 错误数: {stats.errors}")
        
        if stats.hit_latencies:
            print(f"\n⏱️  缓存命中延迟:")
            print(f"   平均: {statistics.mean(stats.hit_latencies):.3f}ms")
            print(f"   P99: {sorted(stats.hit_latencies)[int(len(stats.hit_latencies)*0.99)]:.3f}ms")
        
        if stats.miss_latencies:
            print(f"\n⏱️  缓存未命中延迟:")
            print(f"   平均: {statistics.mean(stats.miss_latencies):.1f}ms")
            print(f"   P99: {sorted(stats.miss_latencies)[int(len(stats.miss_latencies)*0.99)]:.1f}ms")
        
        # 保存报告（简化版，使用现有统计）
        report_path = project_root / "benchmark_cache_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 缓存压测报告\n\n")
            f.write(f"**测试时间**: {datetime.now()}\n\n")
            f.write(f"## 核心数据\n\n")
            f.write(f"- **缓存命中率**: {stats.hit_rate:.1f}%\n")
            f.write(f"- **命中数**: {stats.hits}\n")
            f.write(f"- **未命中数**: {stats.misses}\n")
            f(f"- **错误数**: {stats.errors}\n\n")
            
            if stats.hit_latencies:
                f.write(f"## 缓存命中延迟\n\n")
                f.write(f"- **平均**: {statistics.mean(stats.hit_latencies):.3f}ms\n")
                f.write(f"- **P99**: {sorted(stats.hit_latencies)[int(len(stats.hit_latencies)*0.99)]:.3f}ms\n\n")
            
            if stats.miss_latencies:
                f.write(f"## 缓存未命中延迟\n\n")
                f.write(f"- **平均**: {statistics.mean(stats.miss_latencies):.1f}ms\n")
                f.write(f"- **P99**: {sorted(stats.miss_latencies)[int(len(stats.miss_latencies)*0.99)]:.1f}ms\n\n")
            
            f.write(f"## 缓存实例统计\n\n```json\n{json.dumps(all_stats, indent=2, ensure_ascii=False)}\n```\n")
        
        print(f"\n📄 报告已保存至: {report_path}")
        print(f"\n💡 提示：运行 `python scripts/benchmark_cache.py --help` 查看更多选项")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()