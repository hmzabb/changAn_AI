"""路由监控系统使用示例

演示如何：
1. 查看实时路由统计
2. 导出监控报告
3. 分析可疑案例
"""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.routing_monitor import get_routing_monitor
from app.routers.chat import _route, ChatRequest


async def demo_monitor_usage():
    """演示监控系统的使用方法"""

    print("=" * 70)
    print("📊 路由监控系统使用示例")
    print("=" * 70)

    monitor = get_routing_monitor()

    # 1. 模拟一些路由请求
    print("\n🔄 步骤1: 模拟10次路由请求...")
    test_questions = [
        ("帮我找一家店", "auto"),
        ("博物馆历史", "auto"),
        ("门票价格", "auto"),
        ("有优惠券吗", "agent"),  # 强制模式
        ("景点推荐", "rag"),      # 强制模式
        ("这家店人均多少", "auto"),
        ("西安有什么景点", "auto"),
        ("推荐美食小吃", "auto"),
        ("电话是多少", "auto"),
        ("天气怎么样", "auto"),
    ]

    for question, mode in test_questions:
        req = ChatRequest(session_id="demo-session", message=question, mode=mode)
        result = await _route(req)
        print(f"   ✅ '{question[:20]}' → {result} (mode={mode})")

        # 小延迟模拟真实场景
        await asyncio.sleep(0.01)

    # 2. 查看统计信息
    print("\n📈 步骤2: 查看路由统计信息...")
    stats = monitor.get_stats(time_range_hours=1)

    print(f"\n   📊 路由分布（最近1小时）：")
    print(f"   ┌─────────────────────────────────────┐")
    print(f"   │ 总请求数:     {stats['total_routes']:>6}          │")
    print(f"   │ Agent路由:    {stats['agent_routes']:>6} ({stats['agent_percentage']:>5.1f}%)   │")
    print(f"   │ RAG路由:      {stats['rag_routes']:>6} ({stats['rag_percentage']:>5.1f}%)   │")
    print(f"   ├─────────────────────────────────────┤")
    print(f"   │ 冲突检测:     {stats['conflicts_detected']:>6} ({stats['conflict_percentage']:>5.1f}%)   │")
    print(f"   │ 模式覆盖:     {stats['mode_overrides']:>6}            │")
    print(f"   └─────────────────────────────────────┘")

    print(f"\n   ⏱️  延迟指标：")
    print(f"   ┌─────────────────────────────────────┐")
    print(f"   │ 平均延迟:    {stats['latency_avg_ms']:>8.3f} ms       │")
    print(f"   │ P50延迟:     {stats['latency_p50_ms']:>8.3f} ms       │")
    print(f"   │ P90延迟:     {stats['latency_p90_ms']:>8.3f} ms       │")
    print(f"   │ P99延迟:     {stats['latency_p99_ms']:>8.3f} ms       │")
    print(f"   │ 最小延迟:    {stats['latency_min_ms']:>8.3f} ms       │")
    print(f"   │ 最大延迟:    {stats['latency_max_ms']:>8.3f} ms       │")
    print(f"   └─────────────────────────────────────┘")

    # 3. 查看可疑案例
    print("\n🔍 步骤3: 分析可疑路由案例...")
    suspicious = monitor.get_recent_errors(limit=5)

    if suspicious:
        print(f"\n   发现 {len(suspicious)} 个可疑案例：")
        for i, case in enumerate(suspicious, 1):
            print(f"\n   案例 #{i}:")
            print(f"   ┌─────────────────────────────────────────┐")
            print(f"   │ 问题: {case['message_preview']:<35} │")
            print(f"   │ 路由: {case['route_result']:<4}                      │")
            print(f"   │ 延迟: {case['latency_ms']:.3f}ms                       │")
            print(f"   │ 原因: {', '.join(case.get('suspicion_reasons', [])):<30} │")
            if case.get('conflict_detected'):
                print(f"   │ 冲突解决: {case.get('conflict_resolution', 'N/A'):<24} │")
            print(f"   └─────────────────────────────────────────┘")
    else:
        print("   ✅ 未发现可疑案例，路由系统运行正常！")

    # 4. 导出报告
    print("\n💾 步骤4: 导出监控报告...")
    try:
        monitor.export_report("demo_routing_report.json", format="json")
        print("   ✅ JSON报告已导出: demo_routing_report.json")

        monitor.export_report("demo_routing_report.csv", format="csv")
        print("   ✅ CSV报告已导出: demo_routing_report.csv")
    except Exception as e:
        print(f"   ❌ 导出失败: {e}")

    # 5. API端点说明
    print("\n🌐 步骤5: 可用的API端点...")
    print("""
   ┌──────────────────────────────────────────────────────────────┐
   │ API端点                    │ 方法  │ 说明                     │
   ├──────────────────────────────────────────────────────────────┤
   │ /api/ai/routing/stats      │ GET   │ 获取路由统计数据         │
   │                            │       │ ?time_range_hours=1      │
   ├──────────────────────────────────────────────────────────────┤
   │ /api/ai/routing/suspicious │ GET   │ 获取可疑路由案例         │
   │                            │       │ ?limit=20                │
   ├──────────────────────────────────────────────────────────────┤
   │ /api/ai/routing/export     │ POST  │ 导出监控报告             │
   │                            │       │ ?format=json/csv         │
   └──────────────────────────────────────────────────────────────┘

   使用示例（curl）：

   # 查看最近1小时的统计
   curl http://localhost:8000/api/ai/routing/stats?time_range_hours=1

   # 获取前20个可疑案例
   curl http://localhost:8000/api/ai/routing/suspicious?limit=20

   # 导出JSON格式报告
   curl -X POST http://localhost:8000/api/ai/routing/export?format=json \\
        -o routing_report.json
    """)

    print("\n" + "=" * 70)
    print("✅ 监控系统演示完成！")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(demo_monitor_usage())