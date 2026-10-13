"""Quick cache stats check"""
import sys
sys.path.insert(0, ".")

from app.agent.cache import voucher_cache, shop_cache, vector_cache

for name, c in [
    ("L1_voucher (5s)", voucher_cache),
    ("L2_shop (30s)", shop_cache),
    ("L3_vector (5min)", vector_cache),
]:
    s = c.get_stats()
    print(f"{name}: hits={s['hits']}, misses={s['misses']}, hit_rate={s['hit_rate']}, size={s['size']}, ttl={s['ttl']}s")

total_hits = sum(c.get_stats()["hits"] for c in [voucher_cache, shop_cache, vector_cache])
total_misses = sum(c.get_stats()["misses"] for c in [voucher_cache, shop_cache, vector_cache])
total = total_hits + total_misses
print(f"\nTotal: hits={total_hits}, misses={total_misses}, requests={total}")
if total > 0:
    print(f"Overall hit rate: {total_hits/total*100:.1f}%")

# Also try to collect CachedToolsWrapper stats
try:
    from app.agent.cache import CachedToolsWrapper
    from app.agent.tools import ALL_TOOLS
    wrapper = CachedToolsWrapper(ALL_TOOLS)
    all_stats = wrapper.get_all_cache_stats()
    print("\nPer-tool cache stats:")
    for tool_name, stats in all_stats.items():
        print(f"  {tool_name}: {stats}")
except Exception as e:
    print(f"Wrapper stats error: {e}")