"""验证 overlap=120 的效果：与 overlap=80 对比"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.services.chunking import _split_long, OVERLAP, CHUNK_MAX, chunk_markdown


def test_overlap_120_vs_80():
    """对比 overlap=120 和 80 在长复合句场景下的表现"""
    
    # 构造一个超长复合句（124字）
    long_sentence = (
        "大雁塔位于西安市南郊的大慈恩寺内，始建于唐永徽三年（652年），"
        "是玄奘法师为保存由天竺经丝绸之路带回长安的经卷佛像而主持修建的，"
        "最初只有五层，后来经过多次修缮和重建，现在我们看到的是明代修缮后的"
        "七层楼阁式砖塔，高约64.5米，被视为古都西安的象征。"
    )
    
    # 构造超长文本
    text = long_sentence * 10  # 约1240字
    
    print("=" * 70)
    print(f"测试参数：OVERLAP={OVERLAP}, CHUNK_MAX={CHUNK_MAX}")
    print(f"原句长度：{len(long_sentence)} 字")
    print("=" * 70)
    
    chunks = _split_long(text)
    
    print(f"\n📊 切分结果统计：")
    print(f"   总块数：{len(chunks)}")
    print(f"   总字符数：{sum(len(c) for c in chunks)}")
    print(f"   原文长度：{len(text)}")
    print(f"   冗余率：{(sum(len(c) for c in chunks) - len(text)) / len(text) * 100:.1f}%")
    
    print(f"\n📝 各块详情：")
    for i, chunk in enumerate(chunks):
        print(f"\n--- Chunk {i+1} (长度: {len(chunk)}) ---")
        # 显示前50字和后50字
        if len(chunk) > 100:
            print(f"开头：{chunk[:50]}...")
            print(f"结尾：...{chunk[-50:]}")
        else:
            print(chunk)
    
    # 检查关键信息是否被保留
    print(f"\n🔍 关键信息覆盖检查：")
    keywords = ["玄奘法师", "652年", "丝绸之路", "64.5米", "西安的象征"]
    
    for i, chunk in enumerate(chunks):
        found_keywords = [kw for kw in keywords if kw in chunk]
        if found_keywords:
            print(f"✅ Chunk{i+1} 包含：{found_keywords}")
        
        # 检查overlap区域是否包含完整句子
        if i > 0:
            overlap_region = chunk[:OVERLAP + 20]  # 多取一点看上下文
            has_complete_sentence = '。' in overlap_region or '，' in overlap_region
            if has_complete_sentence:
                print(f"   📌 Overlap区域包含完整句/短语")
            else:
                print(f"   ⚠️  Overlap区域可能不完整")


def test_real_world_scenario():
    """真实文旅场景测试"""
    real_text = (
        "老孙家泡馍是西安最著名的回民街美食之一，始创于清朝末年，至今已有百年历史。"
        "其独特的制作工艺被列入非物质文化遗产名录。店铺位于北院门118号，"
        "营业时间为每天08:00-21:30，人均消费约45元，推荐尝试招牌牛肉泡馍和糖蒜。"
        "需要注意的是，节假日排队时间可能超过1小时，建议提前预约或错峰就餐。"
        "此外，店铺支持微信支付宝付款，但不接受现金，请游客提前准备好电子支付方式。"
        "周边交通十分便利，可乘坐地铁2号线至钟楼站，步行约10分钟即可到达。"
        "如果想要体验更地道的回民街文化，建议晚上前往，此时灯火通明，热闹非凡。"
    ) * 8  # 构造较长文本
    
    print("\n" + "=" * 70)
    print("🌍 真实文旅场景测试")
    print("=" * 70)
    
    chunks = _split_long(real_text)
    
    print(f"\n文本总长度：{len(real_text)} 字")
    print(f"切分块数：{len(chunks)}")
    
    # 模拟用户查询
    test_queries = [
        ("营业时间", ["08:00-21:30"]),
        ("人均消费", ["45元"]),
        ("怎么去", ["地铁2号线", "钟楼站"]),
        ("什么时候去", ["晚上", "节假日"]),
    ]
    
    print(f"\n🎯 用户查询模拟：")
    for query, expected_keywords in test_queries:
        print(f"\n❓ 用户问：'{query}'相关？")
        found_in_chunks = []
        for i, chunk in enumerate(chunks):
            if any(kw in chunk for kw in expected_keywords):
                found_in_chunks.append(i + 1)
        
        if found_in_chunks:
            print(f"   ✅ 命中 Chunk：{found_in_chunks}")
        else:
            print(f"   ❌ 未命中（可能需要优化）")


def compare_overlap_sizes():
    """对比不同 overlap 值的效果"""
    print("\n" + "=" * 70)
    print("📊 不同 Overlap 值对比分析")
    print("=" * 70)
    
    test_text = "这是一句用于测试的话。" * 100  # 约1300字
    
    overlaps_to_test = [0, 40, 80, 120, 150, 200]
    
    results = []
    for ov in overlaps_to_test:
        # 临时修改 OVERLAP（仅用于测试）
        import app.services.chunking as chunking_module
        original_overlap = chunking_module.OVERLAP
        chunking_module.OVERLAP = ov
        
        try:
            chunks = _split_long(test_text)
            total_chars = sum(len(c) for c in chunks)
            redundancy = (total_chars - len(test_text)) / len(test_text) * 100
            
            results.append({
                'overlap': ov,
                'chunks': len(chunks),
                'total_chars': total_chars,
                'redundancy': redundancy,
                'avg_chunk_len': total_chars / len(chunks) if chunks else 0
            })
        finally:
            chunking_module.OVERLAP = original_overlap
    
    print(f"\n{'Overlap':<10} {'块数':<8} {'总字符':<10} {'冗余率':<10} {'平均块长':<10}")
    print("-" * 50)
    for r in results:
        marker = " ← 当前选择" if r['overlap'] == 120 else ""
        print(f"{r['overlap']:<10} {r['chunks']:<8} {r['total_chars']:<10} {r['redundancy']:<9.1f}% {r['avg_chunk_len']:<9.1f}{marker}")


if __name__ == "__main__":
    print("🧪 Overlap=120 验证测试\n")
    
    try:
        test_overlap_120_vs_80()
        test_real_world_scenario()
        compare_overlap_sizes()
        
        print("\n" + "=" * 70)
        print("✅ 所有测试完成")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ 测试出错：{e}")
        import traceback
        traceback.print_exc()