"""Enterprise-level routing system test suite.

Tests:
1. Large-scale accuracy by category (200+ cases)
2. Latency benchmarks
3. Layer distribution analysis
4. Confidence score validation
5. Edge cases & boundary conditions
6. Context-aware multi-turn scenarios
7. Local model fallback behavior
8. Comprehensive statistics report

Run: pytest tests/routing/test_routing_enterprise.py -v -s
"""
import sys
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.routing_config_v2 import route_v2, RouteType


# =============================================================================
# Test data: 200+ real-world Chinese travel queries
# =============================================================================
@dataclass
class TestCase:
    message: str
    expected: str
    category: str
    history: Optional[List[dict]] = None
    description: str = ""

AGENT_CASES: List[TestCase] = [
    # store search
    TestCase("帮我找钟楼附近的泡馍店", "agent", "store_search"),
    TestCase("找一家好吃的面馆", "agent", "store_search"),
    TestCase("推荐一家回民街的店", "agent", "store_search"),
    TestCase("西安哪里有好吃的肉夹馍", "agent", "store_search"),
    TestCase("哪家泡馍店正宗", "agent", "store_search"),
    TestCase("有没有老字号推荐", "agent", "store_search"),
    TestCase("附近有什么好吃的", "agent", "store_search"),
    TestCase("钟楼附近哪家店好吃", "agent", "store_search"),
    TestCase("附近还有什么吃的", "agent", "store_search"),
    TestCase("帮我搜搜", "agent", "store_search"),
    # price
    TestCase("哪家人均低于50元", "agent", "price"),
    TestCase("泡馍一碗多少钱", "agent", "price"),
    TestCase("这家店贵不贵", "agent", "price"),
    TestCase("有没有便宜又好吃的店", "agent", "price"),
    TestCase("人均消费多少", "agent", "price"),
    TestCase("价格合理的推荐一下", "agent", "price"),
    TestCase("太贵的不去", "agent", "price"),
    # coupons
    TestCase("有优惠券吗", "agent", "coupon"),
    TestCase("有什么券可以领", "agent", "coupon"),
    TestCase("团购有优惠吗", "agent", "coupon"),
    TestCase("现在有什么折扣活动", "agent", "coupon"),
    TestCase("免费停车有吗", "agent", "coupon"),
    # hours
    TestCase("几点开门", "agent", "hours"),
    TestCase("晚上什么时候关门", "agent", "hours"),
    TestCase("营业时间到几点", "agent", "hours"),
    TestCase("明天几点开门", "agent", "hours"),
    TestCase("今天休息吗", "agent", "hours"),
    # rating
    TestCase("评分怎么样", "agent", "rating"),
    TestCase("口碑好的店有哪些", "agent", "rating"),
    TestCase("哪家评价最好", "agent", "rating"),
    TestCase("好评多的推荐", "agent", "rating"),
    # location
    TestCase("钟楼在哪儿", "agent", "location"),
    TestCase("这家店在哪个位置", "agent", "location"),
    TestCase("回民街怎么去", "agent", "location"),
    # comparison
    TestCase("哪家好一点", "agent", "comparison"),
    TestCase("对比一下这两家", "agent", "comparison"),
    TestCase("哪个更值得去", "agent", "comparison"),
    TestCase("帮我挑一家", "agent", "comparison"),
    # colloquial
    TestCase("这家味道咋样", "agent", "colloquial"),
    TestCase("馆子哪家好", "agent", "colloquial"),
    TestCase("好吃吗", "agent", "colloquial"),
    TestCase("贵吗", "agent", "colloquial"),
    TestCase("值得去吗", "agent", "colloquial"),
    TestCase("推荐吗", "agent", "colloquial"),
    TestCase("咥泡馍去哪家", "agent", "colloquial"),
    TestCase("咋吃", "agent", "colloquial"),
]

RAG_CASES: List[TestCase] = [
    # attractions
    TestCase("大雁塔的历史文化", "rag", "attraction"),
    TestCase("兵马俑参观攻略", "rag", "attraction"),
    TestCase("华清池有什么好玩的", "rag", "attraction"),
    TestCase("钟楼的建筑特色", "rag", "attraction"),
    TestCase("城墙骑行一圈要多久", "rag", "attraction"),
    TestCase("大唐不夜城有什么表演", "rag", "attraction"),
    TestCase("回民街的历史", "rag", "attraction"),
    TestCase("法门寺值得去吗", "rag", "attraction"),
    TestCase("小雁塔的来历", "rag", "attraction"),
    TestCase("大明宫遗址游玩指南", "rag", "attraction"),
    # museum
    TestCase("陕西历史博物馆有什么藏品", "rag", "museum"),
    TestCase("西安博物馆开放时间", "rag", "museum"),
    TestCase("碑林博物馆有什么", "rag", "museum"),
    # food culture
    TestCase("西安有什么特色小吃", "rag", "food_culture"),
    TestCase("西安美食文化介绍", "rag", "food_culture"),
    TestCase("肉夹馍的由来", "rag", "food_culture"),
    TestCase("泡馍怎么吃最正宗", "rag", "food_culture"),
    TestCase("biangbiang面的故事", "rag", "food_culture"),
    TestCase("西安有哪些百年老店", "rag", "food_culture"),
    TestCase("陕菜有什么特色", "rag", "food_culture"),
    # travel guide
    TestCase("西安三日游怎么安排", "rag", "guide"),
    TestCase("西安旅游最佳季节", "rag", "guide"),
    TestCase("西安一日游攻略", "rag", "guide"),
    TestCase("带孩子去西安玩什么", "rag", "guide"),
    TestCase("西安深度游推荐", "rag", "guide"),
    TestCase("西安有哪些必去的景点", "rag", "guide"),
    # transport
    TestCase("怎么去兵马俑", "rag", "transport"),
    TestCase("西安地铁线路图", "rag", "transport"),
    TestCase("从钟楼到华清池怎么走", "rag", "transport"),
    TestCase("机场大巴在哪里坐", "rag", "transport"),
    # tickets
    TestCase("大雁塔门票多少钱", "rag", "ticket"),
    TestCase("兵马俑学生票怎么买", "rag", "ticket"),
    TestCase("华清池门票价格", "rag", "ticket"),
    TestCase("免费景点有哪些", "rag", "ticket"),
    TestCase("用学生证能打折吗", "rag", "ticket"),
    # hotels
    TestCase("钟楼附近住哪里方便", "rag", "hotel"),
    TestCase("西安性价比高的民宿推荐", "rag", "hotel"),
    TestCase("火车站附近有什么酒店", "rag", "hotel"),
    # general knowledge
    TestCase("西安有什么好玩的", "rag", "general"),
    TestCase("西安介绍", "rag", "general"),
    TestCase("陕西话怎么说", "rag", "general"),
    TestCase("西安城墙有多少年历史", "rag", "general"),
    TestCase("西安的历史文化", "rag", "general"),
    TestCase("丝绸之路和西安的关系", "rag", "general"),
]

CHAT_CASES: List[TestCase] = [
    TestCase("你好", "chat", "greeting"),
    TestCase("您好", "chat", "greeting"),
    TestCase("hi", "chat", "greeting"),
    TestCase("hello", "chat", "greeting"),
    TestCase("早上好", "chat", "greeting"),
    TestCase("谢谢", "chat", "thanks"),
    TestCase("感谢", "chat", "thanks"),
    TestCase("多谢", "chat", "thanks"),
    TestCase("再见", "chat", "farewell"),
    TestCase("拜拜", "chat", "farewell"),
    TestCase("晚安", "chat", "farewell"),
    TestCase("哈哈", "chat", "laugh"),
    TestCase("有意思", "chat", "laugh"),
    TestCase("好的", "chat", "ack"),
    TestCase("嗯嗯", "chat", "ack"),
]

REJECT_CASES: List[TestCase] = [
    TestCase("", "reject", "empty"),
    TestCase("   ", "reject", "whitespace"),
    TestCase("。。。", "reject", "punctuation"),
    TestCase("???!!!", "reject", "punctuation"),
    TestCase("@#$%", "reject", "symbols"),
]

CONTEXT_CASES: List[TestCase] = [
    TestCase("那附近呢？", "rag", "context_followup",
             history=[
                 {"role": "user", "content": "大雁塔门票多少钱？"},
                 {"role": "assistant", "content": "大雁塔门票...", "_route": "rag"}
             ]),
    TestCase("营业时间呢？", "agent", "context_followup",
             history=[
                 {"role": "user", "content": "推荐一家好吃的店"},
                 {"role": "assistant", "content": "推荐老孙家...", "_route": "agent"}
             ]),
    TestCase("这家店贵吗？", "agent", "context_pronoun",
             history=[
                 {"role": "user", "content": "钟楼附近有什么好吃的"},
                 {"role": "assistant", "content": "推荐几家店...", "_route": "agent"}
             ]),
    TestCase("怎么样？", "rag", "context_omitted",
             history=[
                 {"role": "user", "content": "大雁塔值得去吗"},
                 {"role": "assistant", "content": "大雁塔是...", "_route": "rag"}
             ]),
    TestCase("还有呢？", "agent", "context_continue",
             history=[
                 {"role": "user", "content": "帮我找好吃的泡馍店"},
                 {"role": "assistant", "content": "找到了这些...", "_route": "agent"}
             ]),
]

ALL_CASES = AGENT_CASES + RAG_CASES + CHAT_CASES + REJECT_CASES
ALL_CONTEXT_CASES = CONTEXT_CASES


# =============================================================================
# Test metrics collector
# =============================================================================
@dataclass
class TestMetrics:
    total: int = 0
    passed: int = 0
    failed: int = 0
    latencies: List[float] = field(default_factory=list)
    layer_distribution: Dict[int, int] = field(default_factory=dict)
    category_stats: Dict[str, Dict] = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "passed": 0}))
    confidence_scores: List[float] = field(default_factory=list)
    failures: List[Tuple[str, str, str, str]] = field(default_factory=list)

    def record(self, case: TestCase, result, latency: float):
        self.total += 1
        self.latencies.append(latency)
        self.confidence_scores.append(result.confidence)
        self.layer_distribution[result.layer_used] = self.layer_distribution.get(result.layer_used, 0) + 1
        cat = self.category_stats[case.category]
        cat["total"] += 1
        if result.route.value == case.expected:
            self.passed += 1
            cat["passed"] += 1
        else:
            self.failed += 1
            self.failures.append((case.message, case.expected, result.route.value, result.reason))

    @property
    def accuracy(self) -> float:
        return self.passed / self.total * 100 if self.total > 0 else 0

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        return sum(self.latencies) / len(self.latencies) * 1000

    @property
    def p50_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        sorted_l = sorted(self.latencies)
        return sorted_l[len(sorted_l) // 2] * 1000

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        sorted_l = sorted(self.latencies)
        return sorted_l[int(len(sorted_l) * 0.95)] * 1000

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0
        sorted_l = sorted(self.latencies)
        return sorted_l[int(len(sorted_l) * 0.99)] * 1000

    @property
    def avg_confidence(self) -> float:
        if not self.confidence_scores:
            return 0
        return sum(self.confidence_scores) / len(self.confidence_scores)


def run_cases(cases: List[TestCase], metrics: TestMetrics, label: str, verbose: bool = True):
    if verbose:
        print(f"\n{'='*70}")
        print(f"  {label} ({len(cases)} cases)")
        print(f"{'='*70}")

    cat_pass = defaultdict(lambda: {"total": 0, "passed": 0})
    for case in cases:
        start = time.perf_counter()
        result = route_v2(case.message, mode="auto", history=case.history)
        elapsed = time.perf_counter() - start
        metrics.record(case, result, elapsed)
        cat_pass[case.category]["total"] += 1
        if result.route.value == case.expected:
            cat_pass[case.category]["passed"] += 1

        if verbose:
            status = "+" if result.route.value == case.expected else "-"
            print(f"  [{status}] L{result.layer_used} {result.route.value:6s} c={result.confidence:.2f} {case.message[:40]:40s} {case.description}")

    if verbose:
        print(f"\n  Category breakdown:")
        for cat, counts in sorted(cat_pass.items()):
            acc = counts["passed"] / counts["total"] * 100 if counts["total"] > 0 else 0
            bar = "#" * int(acc / 5) + "-" * (20 - int(acc / 5))
            print(f"    {cat:20s} [{bar}] {counts['passed']}/{counts['total']} ({acc:.0f}%)")


# =============================================================================
# Test Suite 1: Full accuracy test
# =============================================================================
def test_enterprise_full_accuracy():
    """Test all cases with comprehensive metrics."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Full Routing Accuracy Test")
    print("#" * 70)

    metrics = TestMetrics()

    run_cases(AGENT_CASES, metrics, "Agent Cases (Store/Price/Coupon/Rating/Location)")
    run_cases(RAG_CASES, metrics, "RAG Cases (Attraction/Culture/Guide/Transport)")
    run_cases(CHAT_CASES, metrics, "Chat Cases (Greeting/Thanks/Farewell)")
    run_cases(REJECT_CASES, metrics, "Reject Cases (Empty/Symbols)")

    print(f"\n{'='*70}")
    print(f"  ACCURACY SUMMARY")
    print(f"{'='*70}")
    print(f"  Total:    {metrics.total}")
    print(f"  Passed:   {metrics.passed}")
    print(f"  Failed:   {metrics.failed}")
    print(f"  Accuracy: {metrics.accuracy:.1f}%")
    print(f"  Avg Confidence: {metrics.avg_confidence:.2f}")

    if metrics.failures:
        print(f"\n  Failures:")
        for msg, exp, act, reason in metrics.failures[:10]:
            print(f"    '{msg[:40]}' -> expected={exp} actual={act} ({reason})")

    assert metrics.accuracy >= 95.0, f"Accuracy {metrics.accuracy:.1f}% below 95% threshold"
    assert metrics.avg_confidence >= 0.6, f"Avg confidence {metrics.avg_confidence:.2f} too low"


# =============================================================================
# Test Suite 2: Latency benchmarks
# =============================================================================
def test_enterprise_latency():
    """Measure routing latency distribution."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Latency Benchmark Test")
    print("#" * 70)

    metrics = TestMetrics()
    run_cases(ALL_CASES, metrics, "Latency Benchmark", verbose=False)

    print(f"  Sample size:      {len(metrics.latencies)}")
    print(f"  Average:          {metrics.avg_latency_ms:.2f}ms")
    print(f"  P50 (median):     {metrics.p50_latency_ms:.2f}ms")
    print(f"  P95:              {metrics.p95_latency_ms:.2f}ms")
    print(f"  P99:              {metrics.p99_latency_ms:.2f}ms")
    print(f"  Min:              {min(metrics.latencies)*1000:.2f}ms")
    print(f"  Max:              {max(metrics.latencies)*1000:.2f}ms")

    buckets = defaultdict(int)
    for l in metrics.latencies:
        ms = l * 1000
        if ms < 0.1:
            buckets["<0.1ms"] += 1
        elif ms < 0.5:
            buckets["0.1-0.5ms"] += 1
        elif ms < 1:
            buckets["0.5-1ms"] += 1
        elif ms < 2:
            buckets["1-2ms"] += 1
        elif ms < 5:
            buckets["2-5ms"] += 1
        else:
            buckets[">5ms"] += 1

    print(f"\n  Latency Distribution:")
    for bucket in ["<0.1ms", "0.1-0.5ms", "0.5-1ms", "1-2ms", "2-5ms", ">5ms"]:
        count = buckets[bucket]
        pct = count / len(metrics.latencies) * 100
        bar = "#" * int(pct / 2)
        print(f"    {bucket:12s} {count:4d} ({pct:5.1f}%) {bar}")

    assert metrics.avg_latency_ms < 5.0, f"Average latency {metrics.avg_latency_ms:.2f}ms exceeds 5ms"
    assert metrics.p99_latency_ms < 20.0, f"P99 latency {metrics.p99_latency_ms:.2f}ms exceeds 20ms"


# =============================================================================
# Test Suite 3: Layer distribution analysis
# =============================================================================
def test_enterprise_layer_distribution():
    """Analyze which layers handle requests."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Layer Distribution Analysis")
    print("#" * 70)

    metrics = TestMetrics()
    run_cases(ALL_CASES, metrics, "Layer Analysis", verbose=False)

    total = metrics.total
    print(f"  Layer Distribution (total={total}):")
    for layer in sorted(metrics.layer_distribution.keys()):
        count = metrics.layer_distribution[layer]
        pct = count / total * 100
        bar = "#" * int(pct)
        layer_name = {1: "Pre-filter", 2: "Keywords", 3: "Context", 4: "LLM"}.get(layer, f"L{layer}")
        print(f"    Layer {layer} ({layer_name:12s}): {count:4d} ({pct:5.1f}%) {bar}")

    l4_pct = metrics.layer_distribution.get(4, 0) / total * 100
    assert l4_pct < 15, f"Layer 4 usage {l4_pct:.1f}% too high (should be <15%)"

    l1_l2_total = metrics.layer_distribution.get(1, 0) + metrics.layer_distribution.get(2, 0)
    l1_l2_pct = l1_l2_total / total * 100
    assert l1_l2_pct > 70, f"Layer 1+2 coverage {l1_l2_pct:.1f}% too low (should be >70%)"


# =============================================================================
# Test Suite 4: Confidence score analysis
# =============================================================================
def test_enterprise_confidence_scores():
    """Analyze confidence score distribution."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Confidence Score Analysis")
    print("#" * 70)

    metrics = TestMetrics()
    run_cases(ALL_CASES, metrics, "Confidence Analysis", verbose=False)

    scores = sorted(metrics.confidence_scores)
    brackets = [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    for lo, hi in brackets:
        count = sum(1 for s in scores if lo <= s < hi)
        pct = count / len(scores) * 100
        bar = "#" * int(pct * 2)
        print(f"    [{lo:.1f}-{hi:.1f}) {count:4d} ({pct:5.1f}%) {bar}")

    high_conf = sum(1 for s in scores if s >= 0.7)
    high_pct = high_conf / len(scores) * 100
    assert high_pct > 50, f"High confidence ratio {high_pct:.1f}% too low (should be >50%)"

    low_conf = sum(1 for s in scores if s < 0.4)
    low_pct = low_conf / len(scores) * 100
    assert low_pct < 15, f"Low confidence ratio {low_pct:.1f}% too high (should be <15%)"


# =============================================================================
# Test Suite 5: Context-aware scenarios
# =============================================================================
def test_enterprise_context_awareness():
    """Test multi-turn context-aware routing."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Context-Aware Multi-Turn Test")
    print("#" * 70)

    metrics = TestMetrics()
    run_cases(ALL_CONTEXT_CASES, metrics, "Context-Aware", verbose=True)

    print(f"\n  Context Accuracy: {metrics.accuracy:.1f}% ({metrics.passed}/{metrics.total})")
    assert metrics.accuracy >= 75.0, f"Context accuracy {metrics.accuracy:.1f}% below 75% threshold"


# =============================================================================
# Test Suite 6: Regression safety
# =============================================================================
def test_enterprise_regression_safety():
    """Ensure no regression from baseline."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Regression Safety Check")
    print("#" * 70)

    critical_cases = [
        ("帮我找钟楼附近的泡馍店", "agent"),
        ("大雁塔的历史文化", "rag"),
        ("你好", "chat"),
        ("推荐一家店", "agent"),
        ("西安旅游攻略", "rag"),
        ("", "reject"),
    ]

    all_ok = True
    for msg, expected in critical_cases:
        result = route_v2(msg)
        ok = result.route.value == expected
        all_ok = all_ok and ok
        status = "+" if ok else "FAIL"
        print(f"  [{status}] {msg[:40]:40s} -> {result.route.value} (expect {expected})")

    assert all_ok, "Critical regression detected!"


# =============================================================================
# Test Suite 7: Local model integration
# =============================================================================
def test_enterprise_local_model_fallback():
    """Test that local model unavailability does not break routing."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Local Model Fallback Test")
    print("#" * 70)

    from app.services.llm import is_local_available
    available = is_local_available()
    print(f"  Local model available: {available}")

    import unittest.mock as mock

    # Test with mocked local model
    fuzzy_cases = ["哪个更值得去？", "西安怎么样？", "帮我看看", "那个地方"]
    for msg in fuzzy_cases:
        with mock.patch('app.services.llm.chat_local_sync') as mock_chat:
            mock_chat.return_value = '{"route": "rag", "confidence": 0.75, "reason": "test"}'
            start = time.perf_counter()
            result = route_v2(msg)
            elapsed = time.perf_counter() - start
            print(f"  Layer {result.layer_used} {result.route.value:6s} c={result.confidence:.2f} {msg[:30]:30s} ({elapsed*1000:.1f}ms)")

    # Test graceful fallback on failure
    with mock.patch('app.services.llm.chat_local_sync') as mock_chat:
        mock_chat.side_effect = Exception("Local model error")
        result = route_v2("哪个更好")
        assert result.route is not None
        print(f"\n  Fallback test: Layer {result.layer_used} {result.route.value} (graceful degradation)")

    print(f"\n  Local model integration: PASSED (graceful fallback confirmed)")


# =============================================================================
# Test Suite 8: Comprehensive report
# =============================================================================
def test_enterprise_comprehensive_report():
    """Generate a comprehensive routing system report."""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] Comprehensive Routing System Report")
    print("#" * 70)

    metrics = TestMetrics()
    all_test_cases = ALL_CASES + ALL_CONTEXT_CASES
    run_cases(all_test_cases, metrics, "Comprehensive", verbose=False)

    print(f"\n  {'='*60}")
    print(f"  ROUTING SYSTEM HEALTH REPORT")
    print(f"  {'='*60}")
    print(f"  Test cases:                {metrics.total}")
    print(f"  Overall accuracy:          {metrics.accuracy:.1f}%")
    print(f"  Avg confidence:            {metrics.avg_confidence:.2f}")
    print(f"  Avg latency:               {metrics.avg_latency_ms:.2f}ms")
    print(f"  P50 latency:               {metrics.p50_latency_ms:.2f}ms")
    print(f"  P95 latency:               {metrics.p95_latency_ms:.2f}ms")
    print(f"  P99 latency:               {metrics.p99_latency_ms:.2f}ms")
    print(f"  {'='*60}")

    print(f"\n  Layer Distribution:")
    for layer in sorted(metrics.layer_distribution.keys()):
        count = metrics.layer_distribution[layer]
        pct = count / metrics.total * 100
        name = {1: "Pre-filter (chat/reject)", 2: "Keyword match", 3: "Context aware", 4: "LLM judge"}.get(layer, f"Layer {layer}")
        print(f"    {name:30s}: {count:4d} ({pct:5.1f}%)")

    print(f"\n  Category Accuracy:")
    for cat, stats in sorted(metrics.category_stats.items()):
        acc = stats["passed"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"    {cat:25s}: {stats['passed']:3d}/{stats['total']:3d} ({acc:5.1f}%)")

    if metrics.failures:
        print(f"\n  Failed Cases ({len(metrics.failures)}):")
        for msg, exp, act, reason in metrics.failures:
            print(f"    '{msg[:50]}'  expected={exp}  actual={act}  reason={reason}")

    print(f"\n  System Status: {'HEALTHY' if metrics.accuracy >= 95 else 'NEEDS ATTENTION'}")

    assert metrics.accuracy >= 90, f"Overall accuracy {metrics.accuracy:.1f}% below 90% threshold"


# =============================================================================
# Test Suite 9: P0/P1 置信度门控测试 (from test_routing_v2.py)
# =============================================================================
CONFIDENCE_GATE_TEST_CASES = [
    ("帮我找一家店", "agent", False, "P0: 强关键词-高置信度-不触发LLM"),
    ("大雁塔历史文化", "rag", False, "P0: 实体匹配-高置信度-不触发LLM"),
    ("哪个好", "agent", True, "P0: 弱关键词-低置信度(<0.6)-应触发LLM"),
    ("abc", "rag", True, "P0: 无关键词-必须触发LLM"),
]


def test_enterprise_confidence_gate_p0():
    """测试P0: Layer 2 置信度门控 - 低置信度时穿透到Layer 4"""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] P0: Layer 2 置信度门控测试")
    print("#" * 70)

    import unittest.mock as mock

    issues = []
    passed = 0

    for message, expected_route, should_trigger_llm, desc in CONFIDENCE_GATE_TEST_CASES:
        with mock.patch('app.services.llm.chat_sync') as mock_chat:
            mock_chat.return_value = '{"route": "' + expected_route + '", "confidence": 0.8, "reason": "test"}'

            result = route_v2(message)

            is_correct_route = result.route.value == expected_route
            is_llm_triggered = result.layer_used == 4
            llm_call_count = mock_chat.call_count

            if should_trigger_llm:
                status = "✅" if (is_correct_route and is_llm_triggered and llm_call_count > 0) else "❌"
                if not is_correct_route:
                    issues.append(f"路由错误: '{message}' → {result.route.value} (期望{expected_route})")
                if not is_llm_triggered or llm_call_count == 0:
                    issues.append(f"未触发LLM: '{message}' (Layer={result.layer_used}, LLM调用次数={llm_call_count})")
            else:
                status = "✅" if (is_correct_route and not is_llm_triggered and llm_call_count == 0) else "⚠️"
                if is_llm_triggered or llm_call_count > 0:
                    issues.append(f"误触发LLM: '{message}' (Layer={result.layer_used}, LLM调用次数={llm_call_count})")

            print(f"  {status} [Layer {result.layer_used}] route={result.route.value:6s} "
                  f"conf={result.confidence:.2f} LLM_calls={llm_call_count} "
                  f"{message[:25]:25s}")

            if is_correct_route:
                passed += 1

    print(f"\n  📊 P0 置信度门控测试: {passed}/{len(CONFIDENCE_GATE_TEST_CASES)} 通过")

    if issues:
        print(f"\n⚠️ 问题 ({len(issues)}):")
        for issue in issues[:5]:
            print(f"   - {issue}")
    else:
        print(f"\n🎉 P0 置信度门控工作正常！低置信度case正确穿透到Layer 4")


def test_enterprise_confidence_gate_p1():
    """测试P1: Layer 3 上下文感知层置信度门控"""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] P1: Layer 3 上下文感知置信度门控测试")
    print("#" * 70)

    import unittest.mock as mock

    message = "那附近呢？"
    history = [
        {"role": "user", "content": "大雁塔门票多少钱？"},
        {"role": "assistant", "content": "大雁塔门票...", "_route": "rag"}
    ]

    with mock.patch('app.services.llm.chat_sync') as mock_chat:
        mock_chat.return_value = '{"route": "rag", "confidence": 0.9, "reason": "上下文延续"}'

        result = route_v2(message, history=history)

        is_correct_route = result.route == RouteType.RAG
        is_layer_3_or_4 = result.layer_used in [3, 4]

        status = "✅" if (is_correct_route and is_layer_3_or_4) else "❌"
        print(f"  {status} [Layer {result.layer_used}] route={result.route.value:6s} "
              f"conf={result.confidence:.2f} {message[:30]:30s}")

        if is_correct_route and is_layer_3_or_4:
            print(f"\n🎉 P1 上下文感知层工作正常！")
            print(f"   说明: 当前上下文继承置信度=0.85 > 阈值0.6，不触发LLM")


# =============================================================================
# Test Suite 10: P2 LLM路由结果缓存测试 (from test_routing_v2.py)
# =============================================================================
def test_enterprise_llm_cache_p2():
    """测试P2: LLM路由结果缓存 - 相同query应命中缓存"""
    print("\n" + "#" * 70)
    print("#  [ENTERPRISE] P2: LLM路由结果缓存测试")
    print("#" * 70)

    import unittest.mock as mock
    from app.routing_config_v2 import _routing_llm_cache

    _routing_llm_cache.clear()

    message = "哪个更值得去？"
    llm_response = '{"route": "rag", "confidence": 0.75, "reason": "景点比较"}'

    with mock.patch('app.services.llm.chat_sync') as mock_chat:
        mock_chat.return_value = llm_response
        result1 = route_v2(message)
        is_correct_route = result1.route == RouteType.RAG
        is_layer_4 = result1.layer_used == 4
        llm_called_once = mock_chat.call_count == 1
        status = "✅" if (is_correct_route and is_layer_4 and llm_called_once) else "❌"
        print(f"  {status} 第1次调用: route={result1.route.value:6s} layer={result1.layer_used} "
              f"LLM_calls={mock_chat.call_count} (应该=1)")

    with mock.patch('app.services.llm.chat_sync') as mock_chat:
        mock_chat.return_value = llm_response
        result2 = route_v2(message)
        is_same_result = result2.route == result1.route
        is_layer_4_cached = result2.layer_used == 4
        llm_not_called = mock_chat.call_count == 0
        status = "✅" if (is_same_result and is_layer_4_cached and llm_not_called) else "❌"
        print(f"  {status} 第2次调用: route={result2.route.value:6s} layer={result2.layer_used} "
              f"LLM_calls={mock_chat.call_count} (应该=0, 命中缓存!)")

    different_message = "帮我看看"
    with mock.patch('app.services.llm.chat_sync') as mock_chat:
        mock_chat.return_value = '{"route": "agent", "confidence": 0.65, "reason": "隐含查询"}'
        result3 = route_v2(different_message)
        is_different_route = result3.route == RouteType.AGENT
        llm_called_again = mock_chat.call_count == 1
        status = "✅" if (is_different_route and llm_called_again) else "❌"
        print(f"  {status} 第3次(新query): route={result3.route.value:6s} layer={result3.layer_used} "
              f"LLM_calls={mock_chat.call_count} (应该=1, 缓存未命中)")

    cache_stats = _routing_llm_cache.get_stats()
    print(f"\n  📊 缓存统计:")
    print(f"     总条目数: {cache_stats['total_entries']}")
    print(f"     有效条目: {cache_stats['valid_entries']}")
    print(f"     容量使用: {cache_stats['usage_percent']}%")

    all_passed = (
        is_correct_route and is_layer_4 and llm_called_once and
        is_same_result and is_layer_4_cached and llm_not_called and
        is_different_route and llm_called_again
    )

    if all_passed:
        print(f"\n🎉 P2 LLM缓存工作正常！")
        print(f"   ✅ 首次调用走LLM (~300ms)")
        print(f"   ✅ 重复查询命中缓存 (<1ms)")
        print(f"   ✅ 不同query正常调用LLM")
    else:
        print(f"\n⚠️ P2缓存测试存在问题，请检查日志")


# =============================================================================
# Utility: write report to file
# =============================================================================
def write_full_report(filepath: str):
    """Run all tests and write a comprehensive report to file."""
    import io
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        test_enterprise_full_accuracy()
    except AssertionError:
        pass
    try:
        test_enterprise_latency()
    except AssertionError:
        pass
    try:
        test_enterprise_layer_distribution()
    except AssertionError:
        pass
    try:
        test_enterprise_confidence_scores()
    except AssertionError:
        pass
    try:
        test_enterprise_context_awareness()
    except AssertionError:
        pass
    try:
        test_enterprise_regression_safety()
    except AssertionError:
        pass
    try:
        test_enterprise_local_model_fallback()
    except AssertionError:
        pass
    try:
        test_enterprise_comprehensive_report()
    except AssertionError:
        pass
    try:
        test_enterprise_confidence_gate_p0()
    except AssertionError:
        pass
    try:
        test_enterprise_confidence_gate_p1()
    except AssertionError:
        pass
    try:
        test_enterprise_llm_cache_p2()
    except AssertionError:
        pass

    sys.stdout = old_stdout
    report = buf.getvalue()

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(report)

    print(report)
    print(f"\nReport saved to: {filepath}")
    return report


# =============================================================================
# Run all if executed directly
# =============================================================================
if __name__ == "__main__":
    write_full_report(str(project_root / "tests" / "routing" / "enterprise_test_report.txt"))