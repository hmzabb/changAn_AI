"""reranker 规则重排测试：关键词加分 / 商圈命中 / MMR 去重。"""
from app.services.reranker import rerank


def _hit(text, similarity, area=None):
    return {"text": text, "metadata": {"area": area}, "similarity": similarity}


def test_keyword_overlap_boost():
    hits = [
        _hit("大雁塔与慈恩寺的历史沿革介绍", 0.55),
        _hit("回民街美食指南：泡馍凉皮肉夹馍", 0.55),
        _hit("无关文本占位", 0.50),  # 陪跑项：让 3 选 2 真正触发重排（2 选 2 会走短路）
    ]
    # 向量分相同，但 query 关键词与第二条重叠更多 → 第二应排第一
    result = rerank("回民街泡馍哪家好吃", hits, top_k=2)
    assert "回民街" in result[0]["text"]


def test_area_match_boost():
    hits = [
        _hit("老孙家泡馍，位于钟楼商圈", 0.60, area="钟楼"),
        _hit("某店铺，位于小寨商圈", 0.62, area="小寨"),
    ]
    result = rerank("钟楼附近有什么好吃的", hits, top_k=2)
    # 向量分低 0.02，但商圈命中 +0.2 → 钟楼店反超
    assert "钟楼" in result[0]["text"]


def test_mmr_dedup_diverse_sources():
    hits = [
        _hit(f"回民街美食指南第{i}节：泡馍凉皮肉夹馍都很好吃，推荐大家去尝尝", 0.60) for i in range(4)
    ] + [_hit("西安城墙骑行攻略，租车指南与最佳时间", 0.58)]
    result = rerank("回民街美食推荐", hits, top_k=3)
    # 前 4 条几乎相同，MMR 应挑出差异化的城墙那条
    texts = " ".join(h["text"] for h in result)
    assert "城墙" in texts


def test_top_k_bounds():
    hits = [_hit(f"文本{i}", 0.5) for i in range(2)]
    assert len(rerank("查询", hits, top_k=4)) == 2  # 不足 top_k 原样返回
