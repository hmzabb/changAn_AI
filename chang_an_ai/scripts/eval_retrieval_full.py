"""RAG 检索质量专业评估。

设计原则：
1. 多相关文档 ground truth（一个 query 可匹配多篇 doc，避免单点标注偏差）
2. 公平重排对比（raw top-K vs rerank top-K，K 值相同才能说明重排效果）
3. 分层指标（按难度分 Layer1-4 独立报告，精准定位短板）
4. NDCG + Hit + MRR 三维度评价（覆盖排序质量、命中率、首位偏好）
5. 边界拒答自动校准阈值（从正常 query 相似度分布取 P5 + margin）

运行：python scripts/eval_retrieval_full.py
输出：scripts/eval_retrieval_full.txt
"""
from __future__ import annotations

import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUTPUT = Path(__file__).with_suffix(".txt")
_PAD = "=" * 64


def log(msg: str) -> None:
    print(msg)
    with open(OUTPUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


OUTPUT.write_text("", encoding="utf-8")


# ============================================================
# Data Structures
# ============================================================
@dataclass
class QAPair:
    query: str
    relevant_docs: list[str]   # 多相关文档（优先顺序）
    layer: int                 # 1=核心事实 2=基础补全 3=进阶推理 4=鲁棒性
    is_boundary: bool = False  # 边界/无关 query（expected=None）


@dataclass
class QueryResult:
    query: str
    expected: list[str]
    layer: int
    is_boundary: bool
    raw_docs: list[str]        # 原始召回 top-8 文档名
    reranked_docs: list[str]   # 重排后 top-K 文档名
    raw_scores: list[float]
    reranked_scores: list[float]
    latency_ms: float
    boundary_rejected: bool = False


@dataclass
class LayerMetrics:
    layer: int
    label: str
    count: int
    hit1: int = 0  # Raw top-4
    hit3: int = 0
    hit4: int = 0
    hit1_rerank: int = 0
    hit3_rerank: int = 0
    hit4_rerank: int = 0
    rr_sum: float = 0.0
    rr_sum_rerank: float = 0.0
    ndcg_sum: float = 0.0
    ndcg_sum_rerank: float = 0.0
    latency_ms: list[float] = field(default_factory=list)


# ============================================================
# QA Pairs（基于 corpus 真实内容逐条人工标注）
# ============================================================
QA_PAIRS: list[QAPair] = [
    # ── Layer 1：核心事实型（25 条）──
    QAPair("大雁塔门票多少钱",     ["大雁塔与大慈恩寺"], 1),
    QAPair("大雁塔是谁主持修建的", ["大雁塔与大慈恩寺"], 1),
    QAPair("大雁塔北广场喷泉什么时候", ["大雁塔与大慈恩寺"], 1),
    QAPair("西安城墙可以骑车吗",   ["西安城墙"], 1),
    QAPair("西安城墙有多长",       ["西安城墙"], 1),
    QAPair("城墙哪个门最值得登",   ["西安城墙"], 1),
    QAPair("钟楼和鼓楼相距多远",   ["钟楼与鼓楼"], 1),
    QAPair("钟楼鼓楼联票多少钱",   ["钟楼与鼓楼"], 1),
    QAPair("大唐不夜城什么时候去最好", ["大唐不夜城"], 1),
    QAPair("不夜城有什么表演",     ["大唐不夜城"], 1),
    QAPair("回民街有哪些必吃小吃", ["回民街美食指南"], 1),
    QAPair("回民街怎么逛不踩坑",   ["回民街美食指南"], 1),
    QAPair("西安三日游怎么安排",   ["西安三日游经典路线"], 1),
    QAPair("兵马俑安排在第几天",   ["西安三日游经典路线"], 1),
    QAPair("西安住宿选哪个商圈方便", ["西安交通与住宿指南"], 1),
    QAPair("去西安坐高铁到哪个站", ["西安交通与住宿指南"], 1),
    QAPair("肉夹馍有什么讲究",     ["西安小吃图鉴"], 1),
    QAPair("西安凉皮有哪几种",     ["西安小吃图鉴"], 1),
    QAPair("biangbiang面是什么",   ["西安小吃图鉴"], 1),
    QAPair("冰峰是什么",           ["西安小吃图鉴"], 1),
    # ── 新增：门票/天气/西线/壶口/华山补充 ──
    QAPair("乾陵门票多少钱",       ["乾陵"], 1),
    QAPair("法门寺供奉的是什么",   ["法门寺"], 1),
    QAPair("华山西峰索道多少钱",   ["西安景点门票与开放时间", "华山"], 1),
    QAPair("壶口瀑布在哪个省",     ["壶口瀑布"], 1),
    QAPair("兵马俑需要提前预约吗", ["西安景点门票与开放时间", "兵马俑"], 1),

    # ── Layer 2：基础补全（21 条）──
    QAPair("大雁塔和大慈恩寺是什么关系",  ["大雁塔与大慈恩寺"], 2),
    QAPair("大雁塔怎么去，地铁几号线",    ["大雁塔与大慈恩寺", "西安交通与住宿指南"], 2),
    QAPair("西安城墙有几个门可以登",       ["西安城墙"], 2),
    QAPair("城墙门票包含自行车吗",         ["西安城墙"], 2),
    QAPair("钟楼鼓楼晚上亮灯吗",           ["钟楼与鼓楼"], 2),
    QAPair("钟楼鼓楼值得单独买票吗",       ["钟楼与鼓楼"], 2),
    QAPair("大唐不夜城需要门票吗",         ["大唐不夜城"], 2),
    QAPair("不夜城适合带孩子去吗",         ["大唐不夜城"], 2),
    QAPair("回民街什么时候人最少",         ["回民街美食指南"], 2),
    QAPair("回民街有哪些坑要注意",         ["回民街美食指南"], 2),
    QAPair("肉夹馍和汉堡有什么区别",       ["西安小吃图鉴"], 2),
    QAPair("西安小吃哪个最辣",             ["西安小吃图鉴"], 2),
    QAPair("三天时间够玩西安吗",           ["西安三日游经典路线"], 2),
    QAPair("咸阳机场到市区怎么走",         ["西安交通与住宿指南"], 2),
    QAPair("西安住宿大概多少钱一晚",       ["西安交通与住宿指南"], 2),
    # ── 新增：天气/门票/西线/壶口补充 ──
    QAPair("乾陵怎么去",                   ["乾陵", "西安交通与住宿指南"], 2),
    QAPair("法门寺和乾陵能一天玩吗",       ["法门寺", "乾陵"], 2),
    QAPair("壶口瀑布冬天能去吗",           ["壶口瀑布"], 2),
    QAPair("陕历博门票怎么预约",           ["西安景点门票与开放时间"], 2),
    QAPair("西安冬天需要穿羽绒服吗",       ["西安天气与穿衣指南"], 2),
    QAPair("西安哪些景点不收门票",         ["西安景点门票与开放时间"], 2),

    # ── Layer 3：进阶推理（17 条）──
    QAPair("从钟楼到大雁塔坐几号地铁，多长时间",
        ["西安交通与住宿指南", "大雁塔与大慈恩寺"], 3),
    QAPair("大雁塔附近有什么好吃的推荐",
        ["大唐不夜城", "大雁塔与大慈恩寺"], 3),
    QAPair("城墙骑行结束后去哪里吃晚饭比较好",
        ["西安城墙", "西安三日游经典路线", "回民街美食指南"], 3),
    QAPair("预算500元以内，三天游西安怎么安排",
        ["西安三日游经典路线"], 3),
    QAPair("带老人和孩子，哪些景点不适合去",
        ["西安三日游经典路线"], 3),
    QAPair("回民街的小吃和普通街头小吃区别在哪",
        ["回民街美食指南"], 3),
    QAPair("如果只有一天时间，优先去哪两个景点",
        ["西安三日游经典路线"], 3),
    QAPair("西安旅游最佳季节是什么时候",
        ["西安交通与住宿指南", "西安三日游经典路线"], 3),
    QAPair("景点之间怎么安排顺序最合理",
        ["西安三日游经典路线"], 3),
    QAPair("哪些小吃是西安独有的",          ["西安小吃图鉴"], 3),
    QAPair("回民街的历史由来是什么",         ["回民街美食指南"], 3),
    QAPair("城墙是中国现存最完整的古城墙吗",  ["西安城墙"], 3),
    # ── 新增：西线/天气/跨文档推理 ──
    QAPair("壶口瀑布和华山哪个更值得去",
        ["壶口瀑布", "华山"], 3),
    QAPair("一月去华山穿什么合适",
        ["西安天气与穿衣指南", "华山"], 3),
    QAPair("乾陵和碑林哪个更值得看",
        ["乾陵", "碑林博物馆"], 3),
    QAPair("行程里有老人，华山怎么安排比较友好",
        ["华山", "西安三日游经典路线"], 3),
    QAPair("西安西线一日游怎么安排",
        ["乾陵", "法门寺"], 3),

    # ── Layer 4：鲁棒性（11 条自然 query + 20 条边界 case）──
    QAPair("咋去大雁塔啊",
        ["大雁塔与大慈恩寺", "西安交通与住宿指南"], 4),
    QAPair("肉夹馍啥味儿的",         ["西安小吃图鉴"], 4),
    QAPair("这地儿有啥好吃的没",     ["回民街美食指南", "西安小吃图鉴"], 4),
    QAPair("大雁它门票多少（错别字）",["大雁塔与大慈恩寺"], 4),
    QAPair("biangbiang面咋写",       ["西安小吃图鉴"], 4),
    QAPair("如果只有两天怎么压缩行程",["西安三日游经典路线"], 4),
    # ── 新增：新股语料口语化/错别字 ──
    QAPair("法门寺有啥好看的",       ["法门寺"], 4),
    QAPair("壶口那地儿冬天能去不",   ["壶口瀑布"], 4),
    QAPair("陕历博咋约",             ["西安景点门票与开放时间"], 4),
    QAPair("西安下雪吗",             ["西安天气与穿衣指南"], 4),
    QAPair("华山门票学生能便宜不",   ["西安景点门票与开放时间", "华山"], 4),

    # ── Boundary Cases（20 条：严格无关 query）──
    QAPair("北京故宫门票多少钱",       [], 4, is_boundary=True),
    QAPair("我想去东京旅游",           [], 4, is_boundary=True),
    QAPair("今天天气怎么样",           [], 4, is_boundary=True),
    QAPair("1+1等于几",               [], 4, is_boundary=True),
    QAPair("介绍一下上海外滩",         [], 4, is_boundary=True),
    QAPair("哪家店的火锅最好吃",       [], 4, is_boundary=True),
    QAPair("成都和重庆哪个好玩",       [], 4, is_boundary=True),
    QAPair("帮我写一封辞职信",         [], 4, is_boundary=True),
    QAPair("明天股市会涨吗",           [], 4, is_boundary=True),
    QAPair("推荐一款手机",             [], 4, is_boundary=True),
    QAPair("双11什么时候开始",        [], 4, is_boundary=True),
    QAPair("Python和Java哪个好",      [], 4, is_boundary=True),
    QAPair("什么是机器学习",           [], 4, is_boundary=True),
    QAPair("帮我翻译一段英文",         [], 4, is_boundary=True),
    QAPair("你能做什么",               [], 4, is_boundary=True),
    # ── 新增：更贴近旅游但非西安的边界 ──
    QAPair("长城在哪个城市",           [], 4, is_boundary=True),
    QAPair("三亚有什么好吃的",         [], 4, is_boundary=True),
    QAPair("去日本旅游需要准备什么",   [], 4, is_boundary=True),
    QAPair("如何办签证",               [], 4, is_boundary=True),
    QAPair("飞机票怎么买便宜",         [], 4, is_boundary=True),
]

# 去重
seen = set()
deduped: list[QAPair] = []
for qa in QA_PAIRS:
    key = (qa.query, qa.is_boundary, tuple(qa.relevant_docs))
    if key not in seen:
        seen.add(key)
        deduped.append(qa)
QA_PAIRS = deduped

# ============================================================
# 评估参数
# ============================================================
RECALL_K = 8       # 原始召回 top-K
RERANK_K = 4       # 重排后 top-K（与原始对比时统一用此值）
MARGIN = 0.10      # 边界阈值 = 正常 query P5 相似度 + MARGIN

# ============================================================
# Step 1: 入库
# ============================================================
log(_PAD)
log("STEP 1: Reset & Ingest")
log(_PAD)

from pymilvus import MilvusClient
from app.config import settings

client = MilvusClient(uri=settings.milvus_uri, timeout=10.0)
if client.has_collection("changan_kb"):
    client.drop_collection("changan_kb")
    log("Dropped existing collection")

from app.repositories.vector_store import get_store
from app.services.ingest_service import load_corpus_chunks
from app.services.embedding import get_embedding_client

store = get_store()
embedder = get_embedding_client()
chunks = load_corpus_chunks()
log(f"Loaded {len(chunks)} chunks from corpus")

all_embeddings = []
for i in range(0, len(chunks), 16):
    batch = chunks[i : i + 16]
    try:
        all_embeddings.extend(embedder.embed_texts([c.text for c in batch]))
        log(f"  Embed batch {i // 16 + 1}: {len(batch)} texts")
    except Exception as e:
        log(f"  Embed batch {i // 16 + 1} FAILED: {e}")
        all_embeddings.extend([[0.0] * 1024] * len(batch))

store.upsert_chunks(chunks, all_embeddings)
log(f"Ingested {len(chunks)} chunks → Milvus count: {store.count()}\n")

# ============================================================
# Step 2: 逐条评估
# ============================================================
log(_PAD)
log("STEP 2: Query Evaluation")
log(_PAD)

from app.services.reranker import rerank

results: list[QueryResult] = []
boundary_scores: list[float] = []     # 边界 query 最高相似度
normal_p5_scores: list[float] = []    # 正常 query P5 百分位相似度

for idx, qa in enumerate(QA_PAIRS):
    t0 = time.perf_counter()
    try:
        qv = embedder.embed_texts([qa.query])[0]
    except Exception:
        log(f"  SKIP [{idx}] embed error: {qa.query[:20]}")
        continue

    hits = store.query(qv, top_k=RECALL_K)
    hits_with_sim = [dict(h, similarity=h["score"]) for h in hits]

    # 原始召回
    raw_docs = [h["metadata"].get("doc", "") for h in hits]
    raw_scores = [h["similarity"] for h in hits_with_sim]

    # 重排（同 K 值对比）
    reranked = rerank(qa.query, hits_with_sim, top_k=max(RECALL_K, RERANK_K))
    reranked_docs = [h["metadata"].get("doc", "") for h in reranked]
    reranked_scores = [h["similarity"] for h in reranked]

    t1 = time.perf_counter()
    latency = (t1 - t0) * 1000

    r = QueryResult(
        query=qa.query,
        expected=qa.relevant_docs,
        layer=qa.layer,
        is_boundary=qa.is_boundary,
        raw_docs=raw_docs,
        reranked_docs=reranked_docs,
        raw_scores=raw_scores,
        reranked_scores=reranked_scores,
        latency_ms=latency,
    )

    if qa.is_boundary:
        max_sim = max(raw_scores) if raw_scores else 0.0
        boundary_scores.append(max_sim)
    else:
        # 收集正常 query P5 相似度（用于自动校准阈值）
        for s in raw_scores[:5]:
            normal_p5_scores.append(s)

    results.append(r)
    log(f"  [{idx:03d}] {qa.layer} {'B' if qa.is_boundary else ' '} {qa.query[:30]} "
        f"→ raw={raw_docs[:3]} | {latency:.0f}ms")

log(f"\nTotal: {len(results)} queries evaluated")

# ── 自动校准边界阈值 ──
if normal_p5_scores:
    sorted_sims = sorted(normal_p5_scores)
    p5_idx = max(0, int(len(sorted_sims) * 0.05) - 1)
    P5 = sorted_sims[p5_idx]
    THRESHOLD = round(P5 + MARGIN, 4)
else:
    THRESHOLD = 0.35
log(f"Auto-calibrated threshold: P5={P5:.4f} + margin {MARGIN} = {THRESHOLD:.4f}")

# 用校准后的阈值判断边界是否拒答
for r in results:
    if r.is_boundary:
        max_sim = max(r.raw_scores) if r.raw_scores else 0.0
        r.boundary_rejected = max_sim < THRESHOLD


# ============================================================
# Step 3: 指标计算
# ============================================================
def _ndcg_at_k(relevant: list[str], retrieved: list[str], k: int) -> float:
    """Binary relevance NDCG@K。"""
    if not relevant:
        return 0.0
    rel_set = set(relevant)
    ideal = [1.0] * min(k, len(relevant))
    dcg_id = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    if dcg_id == 0:
        return 0.0
    dcg = 0.0
    for i, doc in enumerate(retrieved[:k]):
        if doc in rel_set:
            dcg += 1.0 / math.log2(i + 2)
    return dcg / dcg_id


def _evaluate_layer(results: list[QueryResult], top_k: int) -> dict[int, LayerMetrics]:
    """对一层的结果计算所有指标。"""
    layer_map: dict[int, list[QueryResult]] = defaultdict(list)
    for r in results:
        if not r.is_boundary and r.expected:
            layer_map[r.layer].append(r)

    metrics: dict[int, LayerMetrics] = {}
    layer_names = {1: "Core Facts", 2: "Basic", 3: "Advanced", 4: "Robustness"}

    for layer, items in sorted(layer_map.items()):
        m = LayerMetrics(layer=layer, label=layer_names.get(layer, f"L{layer}"), count=len(items))
        for r in items:
            # Raw at top_k
            raw_top = r.raw_docs[:top_k]
            for i, rd in enumerate(raw_top):
                if rd in r.expected:
                    if i == 0:
                        m.hit1 += 1
                    if i < 3:
                        m.hit3 += 1
                    m.hit4 += 1
                    m.rr_sum += 1.0 / (i + 1)
                    break  # MRR 只取第一个命中
            # Reranked at top_k
            rerank_top = r.reranked_docs[:top_k]
            for i, rd in enumerate(rerank_top):
                if rd in r.expected:
                    if i == 0:
                        m.hit1_rerank += 1
                    if i < 3:
                        m.hit3_rerank += 1
                    m.hit4_rerank += 1
                    m.rr_sum_rerank += 1.0 / (i + 1)
                    break
            # NDCG (all positions)
            m.ndcg_sum += _ndcg_at_k(r.expected, r.raw_docs, top_k)
            m.ndcg_sum_rerank += _ndcg_at_k(r.expected, r.reranked_docs, top_k)
            m.latency_ms.append(r.latency_ms)
        metrics[layer] = m
    return metrics


def _overall_metrics(layers: dict[int, LayerMetrics]) -> LayerMetrics:
    """聚合所有层。"""
    m = LayerMetrics(layer=0, label="OVERALL", count=sum(lm.count for lm in layers.values()))
    for lm in layers.values():
        m.hit1 += lm.hit1
        m.hit3 += lm.hit3
        m.hit4 += lm.hit4
        m.hit1_rerank += lm.hit1_rerank
        m.hit3_rerank += lm.hit3_rerank
        m.hit4_rerank += lm.hit4_rerank
        m.rr_sum += lm.rr_sum
        m.rr_sum_rerank += lm.rr_sum_rerank
        m.ndcg_sum += lm.ndcg_sum
        m.ndcg_sum_rerank += lm.ndcg_sum_rerank
        m.latency_ms.extend(lm.latency_ms)
    return m


K = RERANK_K  # 统一用 K=4 公平对比
layer_metrics = _evaluate_layer(results, K)
overall = _overall_metrics(layer_metrics)


# ============================================================
# Step 4: 报告输出
# ============================================================
def pct(num: int, denom: int) -> str:
    return f"{num}/{denom}={num / denom * 100:.1f}%" if denom > 0 else "N/A"


def pct_v(num: float, denom: int) -> str:
    return f"{num / denom * 100:.1f}%" if denom > 0 else "N/A"


log(f"\n{_PAD}")
log("RAG RETRIEVAL QUALITY EVALUATION — PROFESSIONAL REPORT")
log(_PAD)

log(f"\nConfiguration:")
log(f"  Embedding: {settings.embedding_model}")
log(f"  Reranker:  {'bge-reranker-v2-m3 (API)' if settings.rerank_enabled else 'rule-based (keyword+MMR)'}")
log(f"  Recall top-K: {RECALL_K}")
log(f"  Rerank top-K: {RERANK_K} (fair comparison: raw vs rerank both @{K})")
log(f"  Boundary threshold: {THRESHOLD:.4f} (auto: P5 + {MARGIN})")
log(f"  Corpus chunks: {store.count()}")
log(f"  Total queries: {len(results)} ({sum(m.count for m in layer_metrics.values())} normal + {len([r for r in results if r.is_boundary])} boundary)")

# ── 分层明细 ──
for layer in [1, 2, 3, 4]:
    m = layer_metrics.get(layer)
    if not m:
        continue
    log(f"\n{'─' * 48}")
    log(f"Layer {layer}: {m.label} ({m.count} queries)")
    log(f"{'─' * 48}")
    log(f"  {'':>12} {'Raw @4':>12} {'Rerank @4':>12} {'Delta':>12}")
    log(f"  {'Hit@1':>12} {pct(m.hit1, m.count):>12} {pct(m.hit1_rerank, m.count):>12} "
        f"{'+' if m.hit1_rerank >= m.hit1 else ''}{abs(m.hit1_rerank - m.hit1):>10}")
    log(f"  {'Hit@3':>12} {pct(m.hit3, m.count):>12} {pct(m.hit3_rerank, m.count):>12} "
        f"{'+' if m.hit3_rerank >= m.hit3 else ''}{abs(m.hit3_rerank - m.hit3):>10}")
    log(f"  {'Hit@4':>12} {pct(m.hit4, m.count):>12} {pct(m.hit4_rerank, m.count):>12} "
        f"{'+' if m.hit4_rerank >= m.hit4 else ''}{abs(m.hit4_rerank - m.hit4):>10}")
    log(f"  {'MRR':>12} {m.rr_sum / m.count:.3f}       {m.rr_sum_rerank / m.count:.3f}       "
        f"{'+' if m.rr_sum_rerank >= m.rr_sum else ''}{abs(m.rr_sum_rerank - m.rr_sum):.3f}")
    log(f"  {'NDCG@4':>12} {m.ndcg_sum / m.count:.3f}       {m.ndcg_sum_rerank / m.count:.3f}       "
        f"{'+' if m.ndcg_sum_rerank >= m.ndcg_sum else ''}{abs(m.ndcg_sum_rerank - m.ndcg_sum):.3f}")
    latencies = sorted(m.latency_ms)
    log(f"  {'Latency':>12} p50={latencies[len(latencies)//2]:.0f}ms  p95={latencies[int(len(latencies)*0.95)]:.0f}ms")

# ── 汇总 ──
log(f"\n{_PAD}")
log(f"OVERALL SUMMARY (raw vs rerank @{K})")
log(_PAD)
log(f"  {'':>12} {'Raw':>12} {'Rerank':>12} {'Delta':>12}")
log(f"  {'Hit@1':>12} {pct(overall.hit1, overall.count):>12} {pct(overall.hit1_rerank, overall.count):>12} "
    f"{'+' if overall.hit1_rerank >= overall.hit1 else ''}{abs(overall.hit1_rerank - overall.hit1):>10}")
log(f"  {'Hit@3':>12} {pct(overall.hit3, overall.count):>12} {pct(overall.hit3_rerank, overall.count):>12} "
    f"{'+' if overall.hit3_rerank >= overall.hit3 else ''}{abs(overall.hit3_rerank - overall.hit3):>10}")
log(f"  {'Hit@4':>12} {pct(overall.hit4, overall.count):>12} {pct(overall.hit4_rerank, overall.count):>12} "
    f"{'+' if overall.hit4_rerank >= overall.hit4 else ''}{abs(overall.hit4_rerank - overall.hit4):>10}")
log(f"  {'MRR':>12} {overall.rr_sum / overall.count:.3f}       {overall.rr_sum_rerank / overall.count:.3f}       "
    f"{'+' if overall.rr_sum_rerank >= overall.rr_sum else ''}{abs(overall.rr_sum_rerank - overall.rr_sum):.3f}")
log(f"  {'NDCG@4':>12} {overall.ndcg_sum / overall.count:.3f}       {overall.ndcg_sum_rerank / overall.count:.3f}       "
    f"{'+' if overall.ndcg_sum_rerank >= overall.ndcg_sum else ''}{abs(overall.ndcg_sum_rerank - overall.ndcg_sum):.3f}")

# ── 边界拒答 ──
boundary_results = [r for r in results if r.is_boundary]
if boundary_results:
    rejected = sum(1 for r in boundary_results if r.boundary_rejected)
    log(f"\n{_PAD}")
    log("OUT-OF-DOMAIN REJECTION (Boundary Cases)")
    log(_PAD)
    log(f"  Threshold: {THRESHOLD:.4f}")
    log(f"  Rejected:  {rejected}/{len(boundary_results)} = {rejected / len(boundary_results) * 100:.1f}%")
    for r in boundary_results:
        ms = max(r.raw_scores) if r.raw_scores else 0.0
        status = "REJECTED ✓" if r.boundary_rejected else "PASSED ✗"
        log(f"    {status} '{r.query[:30]}' (max_sim={ms:.3f})")

# ── 统计置信度 ──
if overall.count > 0:
    p = overall.hit3_rerank / overall.count
    se = math.sqrt(p * (1 - p) / overall.count) if 0 < p < 1 else 0
    ci = 1.96 * se
    log(f"\n{_PAD}")
    log("STATISTICAL CONFIDENCE (95% CI)")
    log(_PAD)
    log(f"  Hit@3 Rerank: [{max(0, p - ci) * 100:.1f}%, {min(1, p + ci) * 100:.1f}%]")
    log(f"  Interval: ±{ci * 100:.1f}%")
    verdict = (
        "HIGHLY CONFIDENT" if ci <= 0.08
        else "ACCEPTABLE" if ci <= 0.12
        else "LOW — consider more samples"
    )
    log(f"  Verdict: {verdict}")

# ── 逐条明细 ──
log(f"\n{_PAD}")
log("DETAILED PER-QUERY RESULTS")
log(_PAD)
for r in results:
    if r.is_boundary:
        ms = max(r.raw_scores) if r.raw_scores else 0.0
        s = "REJECT" if r.boundary_rejected else "LEAK"
        log(f"  [{s}] B '{r.query[:40]}' max_sim={ms:.3f} | raw={r.raw_docs[:3]}")
    else:
        hit_raw = next((i + 1 for i, d in enumerate(r.raw_docs[:K]) if d in r.expected), None)
        hit_rr = next((i + 1 for i, d in enumerate(r.reranked_docs[:K]) if d in r.expected), None)
        rs = f"raw=#{hit_raw}" if hit_raw else "raw=MISS"
        rrs = f"rerank=#{hit_rr}" if hit_rr else "rerank=MISS"
        if hit_rr and not hit_raw:
            tag = "BOOST  "
        elif hit_raw and not hit_rr:
            tag = "DEGRADE"
        elif hit_raw:
            tag = "OK     "
        else:
            tag = "MISS   "
        log(f"  [{tag}] L{r.layer} '{r.query[:35]}' "
            f"expect={r.expected[0][:15]} | {rs} {rrs} | {r.latency_ms:.0f}ms")

# ── 分数分布（辅助分析） ──
if normal_p5_scores:
    ss = sorted(normal_p5_scores)
    log(f"\n{_PAD}")
    log("SIMILARITY SCORE DISTRIBUTION (Normal Queries, Top-5)")
    log(_PAD)
    percentiles = [0, 5, 10, 25, 50, 75, 90, 95, 100]
    for pct_v_p in percentiles:
        idx = int(len(ss) * pct_v_p / 100)
        idx = min(idx, len(ss) - 1)
        log(f"  P{pct_v_p:>3}: {ss[idx]:.4f}")
    log(f"  Boundary threshold: {THRESHOLD:.4f} (P5 + {MARGIN})")

log(f"\n{_PAD}")
log("EVALUATION COMPLETE")
log(f"Report: {OUTPUT}")
log(_PAD)