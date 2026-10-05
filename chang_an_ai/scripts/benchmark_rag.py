"""RAG Pipeline 性能基准测试：测量 TTFT 和端到端延迟。

运行方式：
    cd chang_an_ai
    .venv/python.exe scripts/benchmark_rag.py

输出：
- TTFT (Time to First Token)：首token时间
- 端到端延迟：完整生成时间
- 各环节耗时分解
- P50/P90/P99 统计
- Markdown 格式报告

注意：
- 会调用真实 API（Embedding / Reranker / LLM），请确保网络通畅
- 默认运行 20 次迭代（可调参）
- 测试结果保存至 benchmark_rag_report.md
"""
from __future__ import annotations

import time
import json
import statistics
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services import rag_service


@dataclass
class RAGTiming:
    """单次RAG请求的计时数据。"""
    query: str
    total_ms: float = 0.0
    ttft_ms: float = 0.0  # Time to First Token (首token时间)
    
    # 各环节耗时
    to_first_status_ms: float = 0.0      # → "正在检索知识库…"
    to_sources_ms: float = 0.0            # → sources 事件
    to_generating_status_ms: float = 0.0  # → "正在生成回答…"
    to_first_delta_ms: float = 0.0        # → 第一个 delta (TTFT)
    to_done_ms: float = 0.0               # → done 事件
    
    # 元数据
    has_fallback: bool = False            # 是否触发 fallback（无检索结果）
    delta_count: int = 0                  # delta 事件数量
    error: Optional[str] = None           # 错误信息
    
    @property
    def is_success(self) -> bool:
        return self.error is None and self.ttft_ms > 0


class RAGBenchmark:
    """RAG 性能基准测试器。"""
    
    def __init__(
        self,
        iterations: int = 20,
        warmup: int = 3,
        test_queries: Optional[List[str]] = None,
    ):
        """
        Args:
            iterations: 正式测试迭代次数
            warmup: 预热次数（不计入统计）
            test_queries: 测试查询列表（None则使用默认）
        """
        self.iterations = iterations
        self.warmup = warmup
        self.test_queries = test_queries or self._default_queries()
        self.results: List[RAGTiming] = []
    
    def _default_queries(self) -> List[str]:
        """默认测试查询（覆盖不同复杂度）。"""
        return [
            # 简单事实型
            "大雁塔门票多少钱",
            "西安城墙几点开门",
            "钟楼和鼓楼有多远",
            
            # 推荐型
            "西安有什么好吃的推荐",
            "三天时间怎么玩西安",
            "回民街有什么特色小吃",
            
            # 比较型
            "大雁塔和钟楼哪个更值得去",
            "肉夹馍和汉堡有什么区别",
            
            # 操作型
            "从机场到大雁塔怎么走",
            "西安住宿建议住在哪里",
            
            # 文化型
            "西安的历史文化简介",
            "兵马俑为什么是世界奇迹",
            
            # 组合型（较复杂）
            "带老人和孩子去西安，有哪些景点适合",
            "预算500元，两天游西安怎么安排",
        ]
    
    def _run_single_query(self, query: str) -> RAGTiming:
        """执行单次RAG查询并计时。"""
        timing = RAGTiming(query=query)
        
        try:
            start_time = time.perf_counter()
            
            event_count = 0
            for event, payload in rag_service.answer(query, []):
                now = time.perf_counter()
                elapsed_ms = (now - start_time) * 1000
                
                if event == "status" and event_count == 0:
                    timing.to_first_status_ms = elapsed_ms
                elif event == "sources":
                    timing.to_sources_ms = elapsed_ms
                    if not payload:  # 空列表 = fallback
                        timing.has_fallback = True
                elif event == "status" and payload == "正在生成回答…":
                    timing.to_generating_status_ms = elapsed_ms
                elif event == "delta":
                    if timing.to_first_delta_ms == 0:
                        timing.to_first_delta_ms = elapsed_ms
                        timing.ttft_ms = elapsed_ms  # TTFT = 首个delta时间
                    timing.delta_count += 1
                elif event == "done":
                    timing.to_done_ms = elapsed_ms
                    timing.total_ms = elapsed_ms
                
                event_count += 1
            
            if timing.ttft_ms == 0 and not timing.has_fallback:
                timing.error = "未收到delta事件且非fallback"
                
        except Exception as e:
            timing.error = str(e)
            timing.total_ms = (time.perf_counter() - start_time) * 1000
        
        return timing
    
    def run(self) -> List[RAGTiming]:
        """执行完整基准测试。"""
        print("=" * 70)
        print("🚀 RAG Pipeline 性能基准测试")
        print("=" * 70)
        print(f"\n📋 配置信息:")
        print(f"   预热次数: {self.warmup}")
        print(f"   正式迭代: {self.iterations}")
        print(f"   测试查询数: {len(self.test_queries)}")
        print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 阶段1：预热
        print(f"\n🔥 阶段1: 预热 ({self.warmup} 次)...")
        for i in range(self.warmup):
            query = self.test_queries[i % len(self.test_queries)]
            print(f"   [{i+1}/{self.warmup}] \"{query[:30]}...\" ", end="")
            timing = self._run_single_query(query)
            status = "✅" if timing.is_success else "❌"
            print(f"{status} ({timing.total_ms:.0f}ms)")
        
        # 阶段2：正式测试
        print(f"\n⚡ 阶段2: 正式测试 ({self.iterations} 次)...")
        self.results = []
        
        for i in range(self.iterations):
            query = self.test_queries[i % len(self.test_queries)]
            print(f"   [{i+1}/{self.iterations}] \"{query[:30]}...\" ", end="")
            
            timing = self._run_single_query(query)
            self.results.append(timing)
            
            status = "✅" if timing.is_success else "❌"
            ttft_str = f"{timing.ttft_ms:.0f}ms" if timing.ttft_ms > 0 else "N/A"
            total_str = f"{timing.total_ms:.0f}ms"
            print(f"{status} TTFT={ttft_str}, 总={total_str}")
        
        return self.results
    
    def generate_report(self) -> str:
        """生成Markdown格式的测试报告。"""
        successful = [r for r in self.results if r.is_success]
        failed = [r for r in self.results if not r.is_success]
        
        if not successful:
            return "# ❌ RAG 基准测试失败\n\n所有请求均未成功完成。\n"
        
        # 计算统计数据
        ttft_values = [r.ttft_ms for r in successful]
        total_values = [r.total_ms for r in successful]
        
        def calc_stats(values: List[float]) -> Dict[str, float]:
            values_sorted = sorted(values)
            return {
                "avg": statistics.mean(values),
                "p50": values_sorted[len(values_sorted) // 2],
                "p90": values_sorted[int(len(values_sorted) * 0.9)],
                "p99": values_sorted[int(len(values_sorted) * 0.99)] if len(values_sorted) > 10 else values_sorted[-1],
                "min": min(values),
                "max": max(values),
                "std": statistics.stdev(values) if len(values) > 1 else 0.0,
            }
        
        ttft_stats = calc_stats(ttft_values)
        total_stats = calc_stats(total_values)
        
        # 各环节平均耗时
        avg_to_retrieve = statistics.mean([r.to_sources_ms for r in successful])
        avg_to_generate = statistics.mean([r.to_generating_status_ms for r in successful])
        avg_first_delta = statistics.mean([r.to_first_delta_ms for r in successful])
        
        report_lines = [
            "# 🔧 RAG Pipeline 性能基准测试报告\n",
            f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"**样本量**: {len(successful)} 次成功请求 (共 {self.iterations} 次)\n",
            f"**失败率**: {len(failed)/self.iterations*100:.1f}% ({len(failed)} 次)\n\n",
            
            "---\n",
            "## 📊 核心指标汇总\n",
            "| 指标 | 平均值 | P50 | P90 | **P99** | 最小值 | 最大值 | 标准差 |",
            "|------|--------|-----|-----|---------|--------|--------|--------|",
            f"| **TTFT (首token时间)** | {ttft_stats['avg']:.1f}ms | {ttft_stats['p50']:.1f}ms | {ttft_stats['p90']:.1f}ms | **{ttft_stats['p99']:.1f}ms** | {ttft_stats['min']:.1f}ms | {ttft_stats['max']:.1f}ms | {ttft_stats['std']:.1f}ms |",
            f"| **端到端延迟** | {total_stats['avg']:.1f}ms | {total_stats['p50']:.1f}ms | {total_stats['p90']:.1f}ms | **{total_stats['p99']:.1f}ms** | {total_stats['min']:.1f}ms | {total_stats['max']:.1f}ms | {total_stats['std']:.1f}ms |\n",
            
            "---\n",
            "## ⏱️ 各环节耗时分解（平均值）\n",
            "| 环节 | 耗时 | 说明 |",
            "|------|------|------|",
            f"| 开始 → 首个status | {statistics.mean([r.to_first_status_ms for r in successful]):.1f}ms | 初始化 + 开始检索 |",
            f"| → sources事件（检索完成） | {avg_to_retrieve:.1f}ms | Query改写 + Embedding + 检索 + 重排 |",
            f"| → '正在生成'status | {avg_to_generate:.1f}ms | 阈值判断 + 构建Prompt |",
            f"| → **首个delta (TTFT)** | **{avg_first_delta:.1f}ms** | **LLM生成第一个token** |",
            f"| → done事件（完成） | {statistics.mean([r.to_done_ms for r in successful]):.1f}ms | 完整生成结束 |\n",
            
            "---\n",
            "## 📈 详细数据\n",
            "### 成功请求列表\n",
            "| 序号 | Query | TTFT(ms) | 总延迟(ms) | Delta数 | Fallback |",
            "|------|-------|----------|------------|---------|----------|",
        ]
        
        for i, r in enumerate(successful, 1):
            fallback = "✅" if r.has_fallback else "❌"
            report_lines.append(
                f"| {i} | {r.query[:25]}... | {r.ttft_ms:.0f} | {r.total_ms:.0f} | {r.delta_count} | {fallback} |"
            )
        
        if failed:
            report_lines.extend([
                "\n### 失败请求\n",
                "| 序号 | Query | 错误信息 |",
                "|------|-------|----------|",
            ])
            for i, r in enumerate(failed, 1):
                error_msg = r.error[:50] if r.error else "未知错误"
                report_lines.append(f"| {i} | {r.query[:25]}... | {error_msg} |")
        
        report_lines.extend([
            "\n---\n",
            "## 💡 分析与建议\n",
        ])
        
        # 智能分析
        if ttft_stats["p99"] < 800:
            report_lines.append("- ✅ **TTFT P99 < 800ms**: 性能优秀，用户体验流畅\n")
        elif ttft_stats["p99"] < 1200:
            report_lines.append("- ⚠️ **TTFT P99 在 800-1200ms**: 性能良好，可进一步优化\n")
        else:
            report_lines.append("- ❌ **TTFT P99 > 1200ms**: 性能待优化，建议检查瓶颈环节\n")
        
        if total_stats["p99"] < 3000:
            report_lines.append("- ✅ **端到端 P99 < 3s**: 流式输出体验好\n")
        elif total_stats["p99"] < 5000:
            report_lines.append("- ⚠️ **端到端 P99 在 3-5s**: 可接受，长答案可能偏慢\n")
        else:
            report_lines.append("- ❌ **端到端 P99 > 5s**: 需要优化，考虑答案截断或并行化\n")
        
        retrieval_time = avg_to_retrieve
        llm_time = avg_first_delta - avg_to_generate
        
        if retrieval_time > llm_time:
            report_lines.append(f"- ⚠️ **检索阶段占比较高** ({retrieval_time:.0f}ms vs LLM {llm_time:.0f}ms)\n")
            report_lines.append("  建议：①检查Embedding/Reranker API延迟 ②考虑本地部署模型 ③增加缓存\n")
        else:
            report_lines.append(f"- ✅ **LLM生成是主要瓶颈** ({llm_time:.0f}ms vs 检索 {retrieval_time:.0f}ms)\n")
            report_lines.append("  这是正常现象，可通过流式输出缓解用户感知延迟\n")
        
        fallback_rate = sum(1 for r in successful if r.has_fallback) / len(successful) * 100
        if fallback_rate > 20:
            report_lines.append(f"- ⚠️ **Fallback率偏高** ({fallback_rate:.1f}%): 可能阈值过严或知识库覆盖不足\n")
        
        report_lines.extend([
            "\n---\n",
            "## 📝 测试方法说明\n",
            "- **TTFT定义**: 从调用`rag_service.answer()`到收到第一个`delta`事件的时间\n",
            "- **端到端延迟**: 从调用开始到收到`done`事件的完整时间\n",
            "- **统计方法**: 剔除预热次数，计算P50/P90/P99分位数\n",
            "- **环境**: 调用真实API（SiliconFlow Embedding/Reranker + DeepSeek LLM）\n",
            "\n---\n",
            f"*报告自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n",
        ])
        
        return "\n".join(report_lines)


def main():
    """主函数。"""
    benchmark = RAGBenchmark(
        iterations=20,    # 20次正式测试
        warmup=3,         # 3次预热
    )
    
    results = benchmark.run()
    report = benchmark.generate_report()
    
    print("\n" + "=" * 70)
    print("📋 测试报告")
    print("=" * 70)
    print(report)
    
    # 保存报告
    report_path = project_root / "benchmark_rag_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 报告已保存至: {report_path}")
    
    # 输出关键数据（方便复制到简历）
    successful = [r for r in results if r.is_success]
    if successful:
        ttft_values = sorted([r.ttft_ms for r in successful])
        p99_idx = int(len(ttft_values) * 0.99)
        p99_ttft = ttft_values[p99_idx]
        
        print("\n" + "=" * 70)
        print("🎯 简历可用数据（真实测试结果）")
        print("=" * 70)
        print(f"\n✅ **RAG Pipeline TTFT P99 = {p99_ttft:.0f}ms**")
        print(f"   （基于{len(successful)}次真实API调用的基准测试）")
        print(f"\n✅ **测试脚本位置**: scripts/benchmark_rag.py")
        print(f"✅ **详细报告**: benchmark_rag_report.md")
        print(f"✅ **可复现性**: 运行 `python scripts/benchmark_rag.py` 即可验证")


if __name__ == "__main__":
    main()