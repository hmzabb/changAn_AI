"""简化版路由全面测试套件（C端场景 + 边界案例）。

测试覆盖：
1. 基础功能测试（正常场景）
2. 边界案例测试（极端/混合意图）
3. 性能基准测试（延迟指标）
4. 并发安全测试（多线程）
5. 特殊字符/编码测试
6. 长文本/超短文本测试
"""

import asyncio
import time
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Dict

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.routers.chat import _route
from app.routers.chat import ChatRequest


class RoutingTestSuite:
    """简化版路由测试套件"""

    def __init__(self):
        self.results: List[Dict] = []
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0

    async def _run_single_test(
        self,
        question: str,
        expected: str,
        category: str,
        description: str
    ) -> bool:
        """执行单个测试用例"""
        self.total_tests += 1
        req = ChatRequest(session_id="test", message=question, mode="auto")

        try:
            start_time = time.perf_counter()
            result = await _route(req)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            success = (result == expected)
            status = "✅" if success else "❌"

            if success:
                self.passed_tests += 1
            else:
                self.failed_tests += 1

            test_result = {
                "status": status,
                "category": category,
                "description": description,
                "question": question[:50] + ("..." if len(question) > 50 else ""),
                "expected": expected,
                "actual": result,
                "latency_ms": round(elapsed_ms, 3),
                "success": success,
            }
            self.results.append(test_result)

            return success

        except Exception as e:
            self.failed_tests += 1
            self.results.append({
                "status": "❌",
                "category": category,
                "description": description,
                "question": question[:50],
                "expected": expected,
                "actual": f"EXCEPTION: {e}",
                "latency_ms": -1,
                "success": False,
            })
            return False

    async def test_basic_functionality(self):
        """测试1: 基础功能（正常C端场景）"""

        print("\n" + "=" * 70)
        print("📋 测试1: 基础功能测试（正常C端场景）")
        print("=" * 70)

        test_cases = [
            # Agent场景：实时数据查询
            ("帮我找一家好吃的店", "agent", "店铺推荐"),
            ("钟楼附近有什么店", "agent", "位置查询-附近"),
            ("哪里有卖泡馍的", "agent", "位置查询-哪里有"),
            ("这家店人均多少", "agent", "价格查询-人均"),
            ("贵不贵", "agent", "价格查询-贵不贵"),
            ("有优惠券吗", "agent", "优惠查询-优惠券"),
            ("有团购吗", "agent", "优惠查询-团购"),
            ("这家店评分怎么样", "agent", "评价查询-评分"),
            ("几点开门", "agent", "营业时间-开门"),
            ("电话是多少", "agent", "联系方式-电话"),

            # RAG场景：静态知识问答
            ("博物馆历史介绍", "rag", "文化知识-历史"),
            ("西安有什么景点", "rag", "景点知识-有什么"),
            ("兵马俑怎么去", "rag", "交通路线-怎么去"),
            ("门票多少钱", "rag", "门票价格"),
            ("开放时间是什么时候", "rag", "开放时间"),
            ("西安特色小吃", "rag", "美食文化-特色小吃"),
            ("老字号有哪些", "rag", "美食文化-老字号"),
            ("最佳旅游时间", "rag", "城市信息-最佳时间"),
            ("天气怎么样", "rag", "城市信息-天气"),
        ]

        for question, expected, desc in test_cases:
            await self._run_single_test(question, expected, "基础功能", desc)

    async def test_edge_cases(self):
        """测试2: 边界案例（混合意图/冲突场景）"""

        print("\n" + "=" * 70)
        print("🔍 测试2: 边界案例测试（混合意图/冲突场景）")
        print("=" * 70)

        test_cases = [
            # 冲突场景→强制RAG
            ("景点门票价格是多少", "rag", "冲突-价格+门票→RAG"),
            ("博物馆门票多少钱", "rag", "冲突-博物馆+门票→RAG"),
            ("学生票价格", "rag", "冲突-价格+学生票→RAG"),
            ("推荐一家博物馆", "rag", "冲突-推荐+博物馆→RAG"),
            ("推荐个景点路线", "rag", "冲突-推荐+景点→RAG"),
            ("推荐美食小吃", "rag", "冲突-推荐+美食→RAG"),

            # 冲突场景→默认Agent
            ("这家店有优惠券吗", "agent", "冲突-店铺+优惠券→Agent"),
            ("博物馆附近有什么店", "agent", "冲突-博物馆+店→Agent"),
            ("景点附近哪家好评多", "agent", "冲突-景点+店+好评→Agent"),

            # 无特征词→兜底RAG
            ("你好", "rag", "无特征-问候"),
            ("谢谢", "rag", "无特征-感谢"),
            ("今天天气不错", "rag", "无特征-闲聊"),
            ("我不知道问什么", "rag", "无特征-困惑"),
            ("abc", "rag", "无特征-无意义"),
            ("", "rag", "无特征-空字符串"),

            # 包含部分关键词
            ("价格", "rag", "单关键词-价格（无上下文）"),
            ("推荐", "rag", "单关键词-推荐（无上下文）"),
            ("店", "rag", "单关键词-店（无上下文）"),
        ]

        for question, expected, desc in test_cases:
            await self._run_single_test(question, expected, "边界案例", desc)

    async def test_special_characters(self):
        """测试3: 特殊字符和编码"""

        print("\n" + "=" * 70)
        print("🔤 测试3: 特殊字符和编码测试")
        print("=" * 70)

        test_cases = [
            ("帮我找一家店！！！", "agent", "多个感叹号"),
            ("这   家   店   怎么   样", "agent", "多余空格"),
            ("帮~我~找~店", "agent", "波浪号分隔"),
            ("推荐一家店？？？", "agent", "中文问号"),
            ("What about 这家店?", "agent", "中英混合"),
            ("帮我找😊店", "agent", "包含emoji"),
            ("\n\n\n帮我找家店\n\n\n", "agent", "换行符"),
            ("  帮 我 找 店  ", "agent", "全角空格"),
        ]

        for question, expected, desc in test_cases:
            await self._run_single_test(question, expected, "特殊字符", desc)

    async def test_length_edge_cases(self):
        """测试4: 长文本和超短文本"""

        print("\n" + "=" * 70)
        print("📏 测试4: 长文本和超短文本测试")
        print("=" * 70)

        # 超长文本（500字）
        long_text = "帮我找" + "一家非常好的" * 100 + "店"
        # 超短文本
        short_texts = [
            ("店", "agent", "单字-店"),
            ("找", "agent", "单字-找"),
            ("啊", "rag", "单字-语气词"),
        ]

        await self._run_single_test(long_text, "agent", "长度边界", "超长文本(500字)")

        for question, expected, desc in short_texts:
            await self._run_single_test(question, expected, "长度边界", desc)

    async def test_performance_benchmark(self):
        """测试5: 性能基准测试"""

        print("\n" + "=" * 70)
        print("⚡ 测试5: 性能基准测试（延迟分布）")
        print("=" * 70)

        # 预热
        for _ in range(10):
            req = ChatRequest(session_id="test", message="测试", mode="auto")
            await _route(req)

        # 正式测试
        latencies = []
        test_questions = [
            "帮我找一家店",
            "博物馆历史",
            "门票价格",
            "有优惠券吗",
            "推荐景点",
        ]

        for _ in range(100):  # 100次迭代
            question = test_questions[_ % len(test_questions)]
            req = ChatRequest(session_id="test", message=question, mode="auto")

            start = time.perf_counter()
            await _route(req)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)

        # 统计分析
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p90 = latencies[int(len(latencies) * 0.9)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        min_lat = min(latencies)

        print(f"\n📊 性能统计（100次请求）：")
        print(f"   平均延迟: {avg:.3f}ms")
        print(f"   P50延迟:  {p50:.3f}ms")
        print(f"   P90延迟:  {p90:.3f}ms")
        print(f"   P99延迟:  {p99:.3f}ms")
        print(f"   最小延迟: {min_lat:.3f}ms")
        print(f"   最大延迟: {max_lat:.3f}ms")

        # 性能断言
        assert p99 < 5.0, f"P99延迟过高: {p99:.3f}ms (应<5ms)"
        assert avg < 1.0, f"平均延迟过高: {avg:.3f}ms (应<1ms)"

        print(f"\n✅ 性能测试通过！所有延迟在可接受范围内")

    async def test_concurrent_safety(self):
        """测试6: 并发安全性"""

        print("\n" + "=" * 70)
        print("🔄 测试6: 并发安全性测试（10并发×10轮）")
        print("=" * 70)

        async def concurrent_worker(worker_id: int):
            """并发工作线程"""
            results = []
            for i in range(10):
                question = f"帮我找{worker_id}号店"
                req = ChatRequest(session_id=f"test-{worker_id}", message=question, mode="auto")
                result = await _route(req)
                results.append(result)
            return results

        # 启动10个并发任务
        tasks = [concurrent_worker(i) for i in range(10)]
        all_results = await asyncio.gather(*tasks)

        # 验证结果
        total_calls = sum(len(r) for r in all_results)
        all_agent = all(r == "agent" for results in all_results for r in results)

        print(f"\n📊 并发测试结果：")
        print(f"   并发数: 10")
        print(f"   每 worker 调用次数: 10")
        print(f"   总调用次数: {total_calls}")
        print(f"   所有结果正确: {'✅ 是' if all_agent else '❌ 否'}")

        assert all_agent, "并发测试失败：存在错误的路由结果"
        print(f"\n✅ 并发安全测试通过！")

    async def test_mode_override(self):
        """测试7: 强制模式覆盖"""

        print("\n" + "=" * 70)
        print("🎛️  测试7: 强制模式覆盖测试")
        print("=" * 70)

        test_cases = [
            ("帮我找一家店", "rag", "强制RAG模式"),
            ("博物馆历史", "agent", "强制Agent模式"),
            ("随便问问", "agent", "强制Agent模式-无特征"),
            ("随便问问", "rag", "强制RAG模式-无特征"),
        ]

        for question, mode, desc in test_cases:
            req = ChatRequest(session_id="test", message=question, mode=mode)
            result = await _route(req)

            success = (result == mode)
            status = "✅" if success else "❌"
            print(f"{status} {desc}: question='{question[:30]}', mode={mode}, result={result}")

            if success:
                self.passed_tests += 1
            else:
                self.failed_tests += 1
            self.total_tests += 1

    def print_summary(self):
        """打印测试总结"""

        print("\n" + "=" * 70)
        print("📊 测试总结报告")
        print("=" * 70)

        print(f"\n✅ 通过: {self.passed_tests}/{self.total_tests} ({self.passed_tests/self.total_tests*100:.1f}%)")
        print(f"❌ 失败: {self.failed_tests}/{self.total_tests} ({self.failed_tests/self.total_tests*100:.1f}%)")

        if self.failed_tests > 0:
            print("\n❌ 失败的测试用例：")
            for r in self.results:
                if not r["success"]:
                    print(f"   [{r['category']}] {r['description']}")
                    print(f"      问题: {r['question']}")
                    print(f"      期望: {r['expected']} | 实际: {r['actual']}")
                    if r["latency_ms"] > 0:
                        print(f"      延迟: {r['latency_ms']}ms")
                    print()

        # 按类别统计
        categories = {}
        for r in self.results:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = {"passed": 0, "failed": 0}
            if r["success"]:
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1

        print("\n📈 分类统计：")
        for cat, stats in categories.items():
            total = stats["passed"] + stats["failed"]
            rate = stats["passed"] / total * 100 if total > 0 else 0
            print(f"   {cat}: {stats['passed']}/{total} ({rate:.0f}%)")

        print("\n" + "=" * 70)

        if self.failed_tests == 0:
            print("🎉 所有测试通过！简化版路由工作完美！")
            return True
        else:
            print(f"⚠️  有 {self.failed_tests} 个测试失败，请检查上述详情")
            return False


async def run_all_tests():
    """运行全部测试套件"""

    suite = RoutingTestSuite()

    print("🚀 开始执行简化版路由全面测试套件")
    print(f"⏰ 开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time()

    try:
        # 按顺序执行所有测试
        await suite.test_basic_functionality()
        await suite.test_edge_cases()
        await suite.test_special_characters()
        await suite.test_length_edge_cases()
        await suite.test_performance_benchmark()
        await suite.test_concurrent_safety()
        await suite.test_mode_override()

    except Exception as e:
        print(f"\n💥 测试套件执行异常: {e}")
        import traceback
        traceback.print_exc()

    end_time = time.time()
    elapsed = end_time - start_time

    print(f"\n⏱️  总耗时: {elapsed:.2f}秒")

    success = suite.print_summary()

    return success


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)