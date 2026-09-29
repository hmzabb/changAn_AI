"""工具性能测试脚本：验证并行化和缓存优化效果。

使用方式：
    python -m app.agent.performance_test

测试内容：
1. 串行 vs 并行工具调用耗时对比
2. 缓存命中率统计
3. 不同并发场景下的性能表现
4. 内存占用监控

输出：
- 每个测试用例的耗时（ms）
- 缓存命中率
- 性能提升百分比
- 建议的TTL配置
"""
from __future__ import annotations

import asyncio
import time
import json
import statistics
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class TestResult:
    """测试结果数据类。"""
    test_name: str
    duration_ms: float
    cache_hits: int = 0
    cache_misses: int = 0
    success: bool = True
    error: str = None

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total * 100) if total > 0 else 0


class ToolPerformanceTester:
    """工具性能测试器。

    提供标准化的性能测试方法，用于对比优化前后的效果。
    """

    def __init__(self):
        self.results: List[TestResult] = []

    def test_serial_execution(self, tools, test_cases: List[Dict]) -> TestResult:
        """测试串行执行性能。

        Args:
            tools: 工具列表
            test_cases: 测试用例列表 [{tool_name, args}]

        Returns:
            测试结果
        """
        print("\n" + "="*60)
        print("📊 测试1: 串行执行")
        print("="*60)

        start_time = time.time()

        for i, case in enumerate(test_cases):
            tool_name = case["tool_name"]
            args = case["args"]

            print(f"  [{i+1}/{len(test_cases)}] 调用 {tool_name}({args})...")

            # 查找并执行工具
            tool_func = next((t for t in tools if t.name == tool_name), None)
            if tool_func:
                result = tool_func.invoke(args)
                print(f"      ✅ 完成 ({len(result)} 字符)")
            else:
                print(f"      ❌ 工具未找到: {tool_name}")

        duration = (time.time() - start_time) * 1000
        result = TestResult(
            test_name="串行执行",
            duration_ms=duration,
            success=True
        )

        print(f"\n⏱️ 总耗时: {duration:.2f} ms")
        print(f"   平均每个工具: {duration/len(test_cases):.2f} ms")

        self.results.append(result)
        return result

    def test_parallel_execution(self, parallel_tool_node, test_cases: List[Dict]) -> TestResult:
        """测试并行执行性能。

        Args:
            parallel_tool_node: ParallelToolNode 实例
            test_cases: 测试用例列表 [{tool_name, args}]

        Returns:
            测试结果
        """
        print("\n" + "="*60)
        print("🚀 测试2: 并行执行")
        print("="*60)

        # 构造模拟的 tool_calls
        tool_calls = []
        for i, case in enumerate(test_cases):
            tool_calls.append({
                "name": case["tool_name"],
                "args": case["args"],
                "id": f"call_{i+1}"
            })
            print(f"  [{i+1}/{len(test_cases)}] 准备调用 {case['tool_name']}({case['args']})")

        # 构造模拟状态
        from langchain_core.messages import AIMessage
        mock_message = AIMessage(content="", tool_calls=tool_calls)
        state = {"messages": [mock_message]}

        start_time = time.time()

        # 执行并行工具节点
        import asyncio
        result_state = asyncio.run(parallel_tool_node.run(state))

        duration = (time.time() - start_time) * 1000
        result = TestResult(
            test_name="并行执行",
            duration_ms=duration,
            success=True
        )

        print(f"\n⏱️ 总耗时: {duration:.2f} ms")
        print(f"   平均每个工具: {duration/len(test_cases):.2f} ms")

        self.results.append(result)
        return result

    def test_cache_effectiveness(self, tools, test_case: Dict, repeat_count: int = 10) -> TestResult:
        """测试缓存有效性。

        Args:
            tools: 带缓存的工具列表
            test_case: 测试用例 {tool_name, args}
            repeat_count: 重复调用次数

        Returns:
            测试结果（含缓存命中/未命中统计）
        """
        print("\n" + "="*60)
        print("💾 测试3: 缓存有效性")
        print("="*60)

        tool_name = test_case["tool_name"]
        args = test_case["args"]

        print(f"  工具: {tool_name}")
        print(f"  参数: {args}")
        print(f"  重复次数: {repeat_count}")

        tool_func = next((t for t in tools if t.name == tool_name), None)
        if not tool_func:
            return TestResult(
                test_name="缓存测试",
                duration_ms=0,
                success=False,
                error=f"工具未找到: {tool_name}"
            )

        start_time = time.time()
        for i in range(repeat_count):
            result = tool_func.invoke(args)
            status = "✅" if i == 0 else "💾(缓存)"
            print(f"  [{i+1}/{repeat_count}] {status} ({len(result)} 字符)")

        duration = (time.time() - start_time) * 1000

        # 获取缓存统计
        from app.agent.cache import CachedToolsWrapper
        wrapper = CachedToolsWrapper(tools)
        stats = wrapper.get_all_cache_stats().get(tool_name, {})

        result = TestResult(
            test_name=f"缓存测试-{tool_name}",
            duration_ms=duration,
            cache_hits=stats.get("hits", 0),
            cache_misses=stats.get("misses", 0),
            success=True
        )

        print(f"\n⏱️ 总耗时: {duration:.2f} ms")
        print(f"   平均每次: {duration/repeat_count:.2f} ms")
        print(f"   缓存命中: {result.cache_hits}")
        print(f"   缓存未命中: {result.cache_misses}")
        print(f"   命中率: {result.cache_hit_rate:.1f}%")

        self.results.append(result)
        return result

    def generate_report(self) -> str:
        """生成性能测试报告。

        Returns:
            Markdown 格式的测试报告
        """
        report = []
        report.append("# 🔧 Agent 工具性能优化报告\n")
        report.append(f"**测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append(f"**测试用例数**: {len(self.results)}\n")

        # 对比串行vs并行
        serial_result = next((r for r in self.results if r.test_name == "串行执行"), None)
        parallel_result = next((r for r in self.results if r.test_name == "并行执行"), None)

        if serial_result and parallel_result:
            improvement = ((serial_result.duration_ms - parallel_result.duration_ms)
                          / serial_result.duration_ms * 100)

            report.append("## 📈 并行化优化效果\n")
            report.append("| 指标 | 串行 | 并行 | 提升 |\n")
            report.append("|------|------|------|------|\n")
            report.append(f"| 总耗时 | {serial_result.duration_ms:.2f}ms | "
                         f"{parallel_result.duration_ms:.2f}ms | **{improvement:.1f}%** |\n")

            if improvement > 40:
                report.append("\n✅ **优秀**: 并行化效果显著，建议在生产环境启用\n")
            elif improvement > 20:
                report.append("\n⚠️ **良好**: 有一定提升，可进一步优化工具粒度\n")
            else:
                report.append("\n❌ **待优化**: 提升不明显，检查是否存在IO密集型瓶颈\n")

        # 缓存统计
        cache_results = [r for r in self.results if "缓存" in r.test_name]
        if cache_results:
            report.append("\n## 💾 缓存效果统计\n")
            report.append("| 工具 | 总耗时 | 平均每次 | 命中率 |\n")
            report.append("|------|--------|---------|--------|\n")

            for r in cache_results:
                avg_time = r.duration_ms / 10  # 假设重复10次
                report.append(f"| {r.test_name} | {r.duration_ms:.2f}ms | "
                             f"{avg_time:.2f}ms | {r.cache_hit_rate:.1f}% |\n")

            avg_hit_rate = statistics.mean([r.cache_hit_rate for r in cache_results])
            report.append(f"\n**平均缓存命中率**: {avg_hit_rate:.1f}%\n")

            if avg_hit_rate > 80:
                report.append("✅ **缓存配置合理**: TTL设置恰当\n")
            elif avg_hit_rate > 50:
                report.append("⚠️ **缓存一般**: 可考虑延长TTL或增加容量\n")
            else:
                report.append("❌ **缓存效率低**: 检查是否参数变化频繁或TTL过短\n")

        # 建议
        report.append("\n## 💡 优化建议\n")
        suggestions = self._generate_suggestions()
        for suggestion in suggestions:
            report.append(f"- {suggestion}\n")

        return "\n".join(report)

    def _generate_suggestions(self) -> List[str]:
        """根据测试结果生成优化建议。"""
        suggestions = []

        # 检查并行化效果
        serial = next((r for r in self.results if r.test_name == "串行执行"), None)
        parallel = next((r for r in self.results if r.test_name == "并行执行"), None)

        if serial and parallel:
            improvement = ((serial.duration_ms - parallel.duration_ms)
                          / serial.duration_ms * 100)

            if improvement < 30:
                suggestions.append("并行化提升不明显，考虑：①减少工具间依赖 ②使用异步HTTP客户端 ③增加批处理接口")

        # 检查缓存命中率
        cache_results = [r for r in self.results if "缓存" in r.test_name]
        if cache_results:
            avg_hit = statistics.mean([r.cache_hit_rate for r in cache_results])
            if avg_hit < 70:
                suggestions.append("缓存命中率偏低，建议：①延长TTL ②统一参数格式 ③检查缓存键生成逻辑")

        # 通用建议
        suggestions.extend([
            "监控生产环境的实际QPS和P99延迟",
            "根据业务峰值调整缓存容量（当前max_size=1000）",
            "考虑引入Redis实现多实例缓存共享（如果部署多副本）",
            "定期审查工具的TTL配置，根据数据更新频率动态调整"
        ])

        return suggestions


async def main():
    """主测试函数。"""
    print("🔧 Agent 工具性能测试")
    print("=" * 60)

    tester = ToolPerformanceTester()

    try:
        # 导入工具
        from app.agent.tools import ALL_TOOLS
        from app.agent.cache import CachedToolsWrapper
        from app.agent.parallel_tools import ParallelToolNode

        # 创建带缓存的工具
        cached_wrapper = CachedToolsWrapper(ALL_TOOLS)
        cached_tools = cached_wrapper.get_cached_tools()

        # 定义测试用例
        test_cases = [
            {"tool_name": "search_shops_by_name", "args": {"name": "泡馍"}},
            {"tool_name": "list_vouchers", "args": {"shop_id": 1}},
            {"tool_name": "search_blogs", "args": {"query": "回民街"}},
        ]

        # 测试1: 串行执行（使用原始工具）
        await tester.test_serial_execution(ALL_TOOLS, test_cases)

        # 测试2: 并行执行
        parallel_node = ParallelToolNode(tools=cached_tools)
        await tester.test_parallel_execution(parallel_node, test_cases)

        # 测试3: 缓存有效性
        await tester.test_cache_effectiveness(
            cached_tools,
            {"tool_name": "get_shop_detail", "args": {"shop_id": 1}},
            repeat_count=10
        )

        # 生成报告
        report = tester.generate_report()
        print("\n" + "="*60)
        print("📋 性能测试报告")
        print("="*60)
        print(report)

        # 保存报告
        with open("performance_report.md", "w", encoding="utf-8") as f:
            f.write(report)
        print("\n✅ 报告已保存到 performance_report.md")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())