"""RAG 快速性能测试（简化版，用于获取真实数据）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import rag_service


def quick_test():
    """运行3次快速测试。"""
    queries = [
        "大雁塔门票多少钱",
        "西安有什么好吃的",
        "三天怎么玩西安",
    ]
    
    print("=" * 60)
    print("🚀 RAG 快速性能测试")
    print("=" * 60)
    
    results = []
    
    for i, query in enumerate(queries, 1):
        print(f"\n[{i}/{len(queries)}] 测试: \"{query}\"")
        print("-" * 40)
        
        start = time.perf_counter()
        ttft = None
        delta_count = 0
        total_time = None
        
        try:
            for event, payload in rag_service.answer(query, []):
                now = time.perf_counter()
                elapsed = (now - start) * 1000
                
                if event == "status":
                    print(f"  ⏱️  [{elapsed:.0f}ms] status: {payload}")
                elif event == "sources":
                    print(f"  📚 [{elapsed:.0f}ms] sources: {len(payload)} 条结果")
                    if not payload:
                        print(f"  ⚠️  触发 Fallback（无检索结果）")
                elif event == "delta":
                    if ttft is None:
                        ttft = elapsed
                        print(f"  ✨ **TTFT = {ttft:.0f}ms** (首token)")
                    delta_count += 1
                    if delta_count <= 3:  # 只打印前3个delta
                        print(f"  💬 [{elapsed:.0f}ms] delta: {payload[:20]}...")
                elif event == "done":
                    total_time = elapsed
                    print(f"  ✅ [{elapsed:.0f}ms] done (完成)")
            
            if ttft is not None and total_time is not None:
                results.append({
                    "query": query,
                    "ttft": ttft,
                    "total": total_time,
                    "deltas": delta_count,
                })
                print(f"\n  📊 结果: TTFT={ttft:.0f}ms, 总耗时={total_time:.0f}ms, Delta数={delta_count}")
            else:
                print(f"\n  ❌ 测试未完整完成")
                
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            import traceback
            traceback.print_exc()
    
    # 汇总
    print("\n" + "=" * 60)
    print("📊 测试汇总")
    print("=" * 60)
    
    if results:
        ttfts = [r["ttft"] for r in results]
        totals = [r["total"] for r in results]
        
        print(f"\n✅ 成功完成: {len(results)}/{len(queries)} 次")
        print(f"\n📈 TTFT (首token时间):")
        print(f"   平均: {sum(ttfts)/len(ttfts):.0f}ms")
        print(f"   最小: {min(ttfts):.0f}ms")
        print(f"   最大: {max(ttfts):.0f}ms")
        print(f"   **P99估算值 ≈ {max(ttfts):.0f}ms** (基于{len(results)}次采样)")
        
        print(f"\n⏱️  端到端延迟:")
        print(f"   平均: {sum(totals)/len(totals):.0f}ms")
        print(f"   最小: {min(totals):.0f}ms")
        print(f"   最大: {max(totals):.0f}ms")
        
        print("\n💡 简历可用数据:")
        print(f"   **TTFT P99 ≈ {max(ttfts):.0f}ms** (基于{len(results)}次真实API调用)")
        print(f"   **测试方法**: 调用rag_service.answer()测量首个delta事件时间")
        print(f"   **可复现**: 运行 python scripts/quick_benchmark.py")
        
    else:
        print("\n❌ 所有测试均失败，请检查:")
        print("   1. API Key是否配置 (.env文件)")
        print("   2. 网络是否通畅 (需要访问DeepSeek/SiliconFlow)")
        print("   3. Milvus向量库是否启动")


if __name__ == "__main__":
    quick_test()