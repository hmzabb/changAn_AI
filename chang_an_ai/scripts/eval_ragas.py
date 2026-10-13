"""RAGAS 评测：忠实度 / 答案相关性 / 上下文精确度 / 上下文召回率。

RAGAS（Retrieval Augmented Generation Assessment）是 RAG 系统的标准评测框架。
本脚本实现四大核心指标 + 检索质量指标 + 分层报告，不依赖 ragas 库。

设计原则：
1. Faithfulness / Answer Relevancy：LLM-as-Judge（Qwen 跨家族评分，避免自评偏差）
2. Context Precision / Context Recall：基于 ground truth 的程序化计算
3. 分层报告：按难度 Layer1-4 独立统计，精准定位短板
4. 检索质量伴随指标：Hit@K / MRR / NDCG 同步输出

用法：
    cd chang_an_ai
    .venv_new/Scripts/python.exe scripts/eval_ragas.py

输出：
    scripts/eval_ragas_<timestamp>.txt（完整报告）
    scripts/eval_ragas_<timestamp>.json（结构化数据）
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.llm import chat_sync, get_client
from app.services.rag_service import answer as rag_answer, retrieve, _build_context
from scripts.qa_pairs import QA_PAIRS, QAPair

# ---- 评测配置 ----
EVAL_MODEL_BASE_URL = settings.qwen_eval_base_url
EVAL_MODEL_API_KEY = settings.qwen_eval_api_key
EVAL_MODEL_NAME = settings.qwen_eval_model
RECALL_K = 8   # 原始召回 top-K
RERANK_K = 5   # 重排后 top-K（对应简历 Top-5 命中率）

TS = int(time.time())
OUTPUT_TXT = Path(__file__).with_name(f"eval_ragas_{TS}.txt")
OUTPUT_JSON = Path(__file__).with_name(f"eval_ragas_{TS}.json")
_PAD = "=" * 72


def log(msg: str) -> None:
    print(msg)
    with open(OUTPUT_TXT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


OUTPUT_TXT.write_text("", encoding="utf-8")


# ============================================================
# Data Structures
# ============================================================
@dataclass
class RAGASResult:
    query: str
    expected_docs: list[str]
    layer: int
    is_boundary: bool
    # RAG 输出
    answer: str
    context_docs: list[str]
    context_texts: list[str]
    retrieved_titles: list[str]
    # RAGAS 四大指标
    faithfulness: float             # 1-5
    answer_relevance: float         # 1-5
    context_precision: float        # 0-1（基于 ground truth 位置加权）
    context_recall: float           # 0-1（expected 中文档被检索到的比例）
    # 检索伴随指标
    hit_1: bool
    hit_3: bool
    hit_k: bool                      # k = RERANK_K
    mrr: float
    ndcg: float
    # 引用准确率
    total_citations: int
    valid_citations: int
    citation_accuracy: float
    # 计时代价
    latency_ms: float
    retrieval_ms: float
    # 是否触发 fallback（无有效检索结果）
    is_fallback: bool = False
    # 边界诊断：检索最高相似度（COSINE）
    max_similarity: float = 0.0


@dataclass
class LayerReport:
    layer: int
    label: str
    count: int
    fallback_count: int
    # RAGAS 指标
    faith_sum: float = 0.0
    faith_vals: list = field(default_factory=list)
    rel_sum: float = 0.0
    rel_vals: list = field(default_factory=list)
    cp_sum: float = 0.0
    cp_vals: list = field(default_factory=list)
    cr_sum: float = 0.0
    cr_vals: list = field(default_factory=list)
    # 检索指标
    hit1_count: int = 0
    hit3_count: int = 0
    hitk_count: int = 0
    mrr_sum: float = 0.0
    ndcg_sum: float = 0.0
    # 引用指标
    total_cit: int = 0
    valid_cit: int = 0
    # 延迟
    latencies: list = field(default_factory=list)


# ============================================================
# LLM-as-Judge 提示词
# ============================================================

FAITHFULNESS_PROMPT = """你是一个严格的 RAG 回答质量评估者。请判断以下 AI 回答是否完全基于给定的【参考上下文】。

评分标准（1-5 分）：
- 1 分：回答完全编造，与上下文毫无关系
- 2 分：回答大部分是编造的，只有零星信息来自上下文
- 3 分：回答部分基于上下文，但有明显添油加醋或偏离
- 4 分：回答基本忠于上下文，只有极少数轻微不准确
- 5 分：回答完全忠实于上下文，没有任何编造或歪曲

【参考上下文】
{context}

【用户问题】
{question}

【AI 回答】
{answer}

请严格只输出一个数字（1-5），不要输出其他任何内容："""


RELEVANCE_PROMPT = """你是一个严格的 RAG 回答质量评估者。请判断以下 AI 回答是否切题、完整地回答了用户问题。

评分标准（1-5 分）：
- 1 分：回答完全答非所问，与问题毫无关系
- 2 分：回答勉强沾边，但大部分内容不相关或跑题
- 3 分：回答部分切题，但遗漏了关键信息或包含无关内容
- 4 分：回答基本切题，回答了问题的核心，但可以更完整
- 5 分：回答完全切题，直接、完整、准确地回答了用户问题

【用户问题】
{question}

【AI 回答】
{answer}

请严格只输出一个数字（1-5），不要输出其他任何内容："""


# ============================================================
# Judge 调用（Qwen，跨家族避免自评偏差）
# ============================================================

_eval_client = None
_deepseek_client = None
_qwen_failed = False


def _get_eval_client():
    global _eval_client
    if _eval_client is None:
        from openai import OpenAI
        _eval_client = OpenAI(
            base_url=EVAL_MODEL_BASE_URL,
            api_key=EVAL_MODEL_API_KEY,
        )
    return _eval_client


def _get_deepseek_fallback_client():
    global _deepseek_client
    if _deepseek_client is None:
        from openai import OpenAI
        from app.config import settings
        _deepseek_client = OpenAI(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
        )
    return _deepseek_client


def _call_judge(prompt: str) -> float:
    global _qwen_failed
    try:
        if not _qwen_failed:
            client = _get_eval_client()
            model = EVAL_MODEL_NAME
        else:
            client = _get_deepseek_fallback_client()
            from app.config import settings
            model = settings.deepseek_model
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=60.0,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"([1-5])", raw)
        if match:
            return float(match.group(1))
        return 0.0
    except Exception as e:
        log(f"    Judge error: {e}")
        if not _qwen_failed:
            _qwen_failed = True
            log("    (Qwen不可用, 降级为DeepSeek评测)")
            return _call_judge(prompt)
        log("    (DeepSeek评测也失败)")
        return 0.0


# ============================================================
# Context Precision / Recall（程序化计算）
# ============================================================

def _normalize_title(title: str) -> str:
    """归一化文档标题：去扩展名、去括号变体、去空格。"""
    t = title.strip().lower()
    # 去掉文件扩展名
    for ext in [".md", ".pdf", ".docx", ".txt"]:
        if t.endswith(ext):
            t = t[:-len(ext)]
    return t


def _match_doc(retrieved_title: str | dict, expected_docs: list[str]) -> bool:
    """检查检索到的标题是否匹配任一期望文档。"""
    # 防御：某些 metadata 中 title 可能是嵌套 dict
    if isinstance(retrieved_title, dict):
        retrieved_title = retrieved_title.get("title") or retrieved_title.get("name") or str(retrieved_title)
    rt = _normalize_title(retrieved_title)
    for expected in expected_docs:
        et = _normalize_title(expected)
        if rt == et or et in rt or rt in et:
            return True
    return False


def _calc_context_precision(
    retrieved_titles: list[str], expected_docs: list[str], k: int = RERANK_K
) -> float:
    """Context Precision@K：基于 ground truth 位置加权。

    CP@K = sum_{i=1..K} (is_relevant_i * precision_at_i) / total_relevant_in_topK

    其中 precision_at_i = sum_{j=1..i} is_relevant_j / i
    """
    retrieved = retrieved_titles[:k]
    relevant_at_k = [1 if _match_doc(t, expected_docs) else 0 for t in retrieved]
    total_relevant = sum(relevant_at_k)
    if total_relevant == 0:
        return 0.0

    cp_sum = 0.0
    for i in range(len(relevant_at_k)):
        if relevant_at_k[i]:
            precision_at_i = sum(relevant_at_k[:i + 1]) / (i + 1)
            cp_sum += precision_at_i
    return cp_sum / total_relevant


def _calc_context_recall(
    retrieved_titles: list[str], expected_docs: list[str], k: int = RERANK_K
) -> float:
    """Context Recall@K：期望文档中被检索到的比例。

    CR@K = |retrieved_topK ∩ expected| / |expected|
    """
    if not expected_docs:
        return 0.0
    matched = sum(
        1 for e in expected_docs
        if any(_match_doc(t, [e]) for t in retrieved_titles[:k])
    )
    return matched / len(expected_docs)


def _calc_dcg(relevances: list[int], k: int = RERANK_K) -> float:
    """DCG@K"""
    dcg = 0.0
    for i in range(min(k, len(relevances))):
        rel = relevances[i]
        dcg += (2 ** rel - 1) / math.log2(i + 2)
    return dcg


def _calc_ndcg(retrieved_titles: list[str], expected_docs: list[str], k: int = RERANK_K) -> float:
    """NDCG@K：基于 ground truth 二值相关性。"""
    relevance = [1 if _match_doc(t, expected_docs) else 0 for t in retrieved_titles]
    dcg = _calc_dcg(relevance, k)
    # ideal DCG：所有相关文档排在前面
    ideal_relevance = sorted(relevance, reverse=True)
    idcg = _calc_dcg(ideal_relevance, k)
    return dcg / idcg if idcg > 0 else 0.0


def _calc_mrr(retrieved_titles: list[str], expected_docs: list[str]) -> float:
    """MRR：第一个相关文档的排名倒数。"""
    for i, t in enumerate(retrieved_titles):
        if _match_doc(t, expected_docs):
            return 1.0 / (i + 1)
    return 0.0


# ============================================================
# 引用准确率检查
# ============================================================

def _check_citations(answer: str, context_count: int) -> tuple[int, int]:
    citations = re.findall(r"\[(\d+)\]", answer)
    if not citations:
        return 0, 0
    total = len(citations)
    valid = sum(1 for c in citations if 1 <= int(c) <= context_count)
    return total, valid


# ============================================================
# 单条评测
# ============================================================

def evaluate_single(query: str, expected_docs: list[str], layer: int,
                    is_boundary: bool) -> RAGASResult:
    t0 = time.perf_counter()

    # ---- 检索 ----
    t_ret_start = time.perf_counter()
    hits = retrieve(query)
    max_similarity = max((h.get("milvus_sim", h.get("similarity", 0)) for h in hits), default=0.0)
    valid_hits = [h for h in hits if h.get("milvus_sim", h.get("similarity", 0)) >= settings.rag_min_score]
    t_ret = (time.perf_counter() - t_ret_start) * 1000

    retrieved_titles = [
        h.get("metadata", {}).get("title", h.get("metadata", {}).get("doc", ""))
        for h in hits[:RECALL_K]
    ]
    reranked_titles = [
        h.get("metadata", {}).get("title", h.get("metadata", {}).get("doc", ""))
        for h in valid_hits[:RERANK_K]
    ]

    # ---- RAG 回答 ----
    full_context = _build_context(valid_hits) if valid_hits else ""
    events = list(rag_answer(query, []))
    answer = ""
    context_docs = []
    context_texts = []
    sources_count = 0

    for event_type, payload in events:
        if event_type == "sources":
            sources_count = len(payload) if payload else 0
            context_docs = [s.get("title", "") for s in payload] if payload else []
        elif event_type == "delta":
            answer += payload if isinstance(payload, str) else ""

    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000
    is_fallback = (sources_count == 0 or not answer.strip())

    # ---- 边界/fallback 直接返回 ----
    if is_fallback:
        return RAGASResult(
            query=query,
            expected_docs=expected_docs,
            layer=layer,
            is_boundary=is_boundary,
            answer=answer.strip() or "(FALLBACK)",
            context_docs=[],
            context_texts=[],
            retrieved_titles=retrieved_titles,
            faithfulness=0.0,
            answer_relevance=0.0,
            context_precision=0.0,
            context_recall=0.0,
            hit_1=False, hit_3=False, hit_k=False,
            mrr=0.0, ndcg=0.0,
            total_citations=0, valid_citations=0, citation_accuracy=0.0,
            latency_ms=latency_ms, retrieval_ms=t_ret,
            is_fallback=True,
            max_similarity=max_similarity,
        )

    # ---- RAGAS 指标 ----
    # Faithfulness（Qwen judge）
    faith_prompt = FAITHFULNESS_PROMPT.format(
        context=full_context[:6000],
        question=query,
        answer=answer,
    )
    faithfulness = _call_judge(faith_prompt)

    # Answer Relevance（Qwen judge）
    rel_prompt = RELEVANCE_PROMPT.format(
        question=query,
        answer=answer,
    )
    answer_relevance = _call_judge(rel_prompt)

    # Context Precision & Recall（程序化）
    context_precision = _calc_context_precision(reranked_titles, expected_docs)
    # 对找回的原始8条也算召回
    all_context_titles = [
        h.get("metadata", {}).get("title", h.get("metadata", {}).get("doc", ""))
        for h in hits[:RECALL_K]
    ]
    context_recall = _calc_context_recall(all_context_titles, expected_docs, RECALL_K)

    # ---- 检索伴随指标 ----
    hit_1 = any(_match_doc(t, expected_docs) for t in reranked_titles[:1])
    hit_3 = any(_match_doc(t, expected_docs) for t in reranked_titles[:3])
    hit_k = any(_match_doc(t, expected_docs) for t in reranked_titles[:RERANK_K])
    mrr = _calc_mrr(reranked_titles, expected_docs)
    ndcg = _calc_ndcg(reranked_titles, expected_docs, RERANK_K)

    # ---- 引用准确率 ----
    total_cit, valid_cit = _check_citations(answer, sources_count)
    citation_accuracy = valid_cit / total_cit if total_cit > 0 else 0.0

    return RAGASResult(
        query=query,
        expected_docs=expected_docs,
        layer=layer,
        is_boundary=is_boundary,
        answer=answer,
        context_docs=context_docs,
        context_texts=[full_context],
        retrieved_titles=all_context_titles,
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
        context_precision=context_precision,
        context_recall=context_recall,
        hit_1=hit_1, hit_3=hit_3, hit_k=hit_k,
        mrr=mrr, ndcg=ndcg,
        total_citations=total_cit, valid_citations=valid_cit,
        citation_accuracy=citation_accuracy,
        latency_ms=latency_ms, retrieval_ms=t_ret,
        is_fallback=False,
        max_similarity=max_similarity,
    )


# ============================================================
# 分层统计
# ============================================================

def build_layer_reports(results: list[RAGASResult]) -> dict[int, LayerReport]:
    layer_map: dict[int, list[RAGASResult]] = defaultdict(list)
    for r in results:
        layer_map[r.layer].append(r)

    layer_names = {1: "Layer1-核心事实", 2: "Layer2-基础补全",
                   3: "Layer3-进阶推理", 4: "Layer4-鲁棒性"}

    reports: dict[int, LayerReport] = {}
    for layer in [1, 2, 3, 4]:
        items = layer_map.get(layer, [])
        rpt = LayerReport(
            layer=layer,
            label=layer_names[layer],
            count=len(items),
            fallback_count=sum(1 for r in items if r.is_fallback),
        )
        for r in items:
            rpt.latencies.append(r.latency_ms)
            if r.is_fallback:
                continue
            rpt.faith_sum += r.faithfulness
            rpt.faith_vals.append(r.faithfulness)
            rpt.rel_sum += r.answer_relevance
            rpt.rel_vals.append(r.answer_relevance)
            rpt.cp_sum += r.context_precision
            rpt.cp_vals.append(r.context_precision)
            rpt.cr_sum += r.context_recall
            rpt.cr_vals.append(r.context_recall)
            if r.hit_1: rpt.hit1_count += 1
            if r.hit_3: rpt.hit3_count += 1
            if r.hit_k: rpt.hitk_count += 1
            rpt.mrr_sum += r.mrr
            rpt.ndcg_sum += r.ndcg
            rpt.total_cit += r.total_citations
            rpt.valid_cit += r.valid_citations

        reports[layer] = rpt
    return reports


def _avg(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _rate(n: int, total: int) -> str:
    return f"{n / total:.1%}" if total > 0 else "N/A"


def print_reports(reports: dict[int, LayerReport], results: list[RAGASResult]) -> dict:
    all_items = [r for r in results if not r.is_boundary]
    total_active = sum(1 for r in all_items if not r.is_fallback)
    total_all = len(all_items)

    log(f"\n{_PAD}")
    log("PER-LAYER RAGAS REPORT")
    log(_PAD)

    for layer in [1, 2, 3, 4]:
        rpt = reports[layer]
        if rpt.count == 0:
            continue
        active = rpt.count - rpt.fallback_count

        log(f"\n{'─' * 56}")
        log(f"  {rpt.label} ({rpt.count} 条, fallback {rpt.fallback_count} 条)")
        log(f"{'─' * 56}")

        if active == 0:
            log("  (全部触发 fallback，无有效评测结果)")
            continue

        # RAGAS 核心四指标
        avg_f = _avg(rpt.faith_vals)
        avg_r = _avg(rpt.rel_vals)
        avg_cp = _avg(rpt.cp_vals)
        avg_cr = _avg(rpt.cr_vals)
        pct_f_ge4 = sum(1 for v in rpt.faith_vals if v >= 4) / active * 100 if active else 0
        pct_r_ge4 = sum(1 for v in rpt.rel_vals if v >= 4) / active * 100 if active else 0

        log(f"  Faithfulness:        {avg_f:.2f}/5 ({avg_f/5:.2f} norm)  (≥4: {pct_f_ge4:.0f}%)  目标 ≥4.0")
        log(f"  Answer Relevance:    {avg_r:.2f}/5 ({avg_r/5:.2f} norm)  (≥4: {pct_r_ge4:.0f}%)  目标 ≥4.0")
        log(f"  Context Precision:   {avg_cp:.3f}  目标 ≥0.70")
        log(f"  Context Recall:      {avg_cr:.3f}  目标 ≥0.80")

        # 检索伴随指标
        log(f"  Hit@1: {_rate(rpt.hit1_count, active)}  "
            f"Hit@3: {_rate(rpt.hit3_count, active)}  "
            f"Hit@{RERANK_K}: {_rate(rpt.hitk_count, active)}")
        mrr_avg = rpt.mrr_sum / active if active else 0
        ndcg_avg = rpt.ndcg_sum / active if active else 0
        log(f"  MRR: {mrr_avg:.3f}  NDCG@{RERANK_K}: {ndcg_avg:.3f}")

        # 引用
        cite_acc = rpt.valid_cit / rpt.total_cit if rpt.total_cit > 0 else 0
        log(f"  Citation Accuracy:   {rpt.valid_cit}/{rpt.total_cit} ({cite_acc:.1%})")

        # 延迟
        avg_lat = _avg(rpt.latencies)
        sorted_lat = sorted(rpt.latencies)
        n_l = len(sorted_lat)
        p50 = sorted_lat[n_l // 2] if sorted_lat else 0
        p90 = sorted_lat[int(n_l * 0.90)] if n_l > 0 and int(n_l * 0.90) < n_l else (sorted_lat[-1] if sorted_lat else 0)
        p95 = sorted_lat[int(n_l * 0.95)] if n_l > 0 and int(n_l * 0.95) < n_l else (sorted_lat[-1] if sorted_lat else 0)
        p99 = sorted_lat[int(n_l * 0.99)] if n_l > 0 and int(n_l * 0.99) < n_l else (sorted_lat[-1] if sorted_lat else 0)
        log(f"  Latency: avg={avg_lat:.0f}ms  P50={p50:.0f}ms  P90={p90:.0f}ms  P95={p95:.0f}ms  P99={p99:.0f}ms")

        # 幻觉分布
        hall = sum(1 for v in rpt.faith_vals if 0 < v <= 2)
        partial = sum(1 for v in rpt.faith_vals if 2 < v <= 3)
        good = sum(1 for v in rpt.faith_vals if v > 3)
        log(f"  Faith分布: HALLUC={hall}  PARTIAL={partial}  GOOD={good}")

    # ---- 全局汇总 ----
    log(f"\n{_PAD}")
    log("GLOBAL SUMMARY")
    log(_PAD)

    all_faith = []
    all_rel = []
    all_cp = []
    all_cr = []
    all_hit1 = 0
    all_hit3 = 0
    all_hitk = 0
    all_mrr = 0.0
    all_ndcg = 0.0
    all_cit_t = 0
    all_cit_v = 0
    all_lat = []
    fallback_total = 0

    for rpt in reports.values():
        all_faith.extend(rpt.faith_vals)
        all_rel.extend(rpt.rel_vals)
        all_cp.extend(rpt.cp_vals)
        all_cr.extend(rpt.cr_vals)
        all_hit1 += rpt.hit1_count
        all_hit3 += rpt.hit3_count
        all_hitk += rpt.hitk_count
        all_mrr += rpt.mrr_sum
        all_ndcg += rpt.ndcg_sum
        all_cit_t += rpt.total_cit
        all_cit_v += rpt.valid_cit
        all_lat.extend(rpt.latencies)
        fallback_total += rpt.fallback_count

    log(f"  总评测数: {total_all} 条 (fallback {fallback_total} 条)")
    log(f"  有效评测: {total_active} 条")
    log(f"  评分模型: {EVAL_MODEL_NAME}" +
        (" (跨家族，避免自评偏差)" if not _qwen_failed else " (Qwen不可用，降级DeepSeek评测)"))
    log("")

    log(f"  Faithfulness:        {_avg(all_faith):.2f}/5 ({_avg(all_faith)/5:.2f} norm)  目标 ≥4.0")
    log(f"  Answer Relevance:    {_avg(all_rel):.2f}/5 ({_avg(all_rel)/5:.2f} norm)  目标 ≥4.0")
    log(f"  Context Precision:   {_avg(all_cp):.3f}  目标 ≥0.70")
    log(f"  Context Recall:      {_avg(all_cr):.3f}  目标 ≥0.80")
    log("")
    log(f"  Hit@1: {_rate(all_hit1, total_active)}  "
        f"Hit@3: {_rate(all_hit3, total_active)}  "
        f"Hit@{RERANK_K}: {_rate(all_hitk, total_active)}")
    log(f"  MRR: {all_mrr / total_active:.3f}" if total_active else "  MRR: N/A")
    log(f"  NDCG@{RERANK_K}: {all_ndcg / total_active:.3f}" if total_active else "  NDCG: N/A")
    log("")
    cite_acc = all_cit_v / all_cit_t if all_cit_t > 0 else 0
    log(f"  Citation Accuracy:   {all_cit_v}/{all_cit_t} ({cite_acc:.1%})")
    log("")
    avg_lat = _avg(all_lat)
    sorted_lat = sorted(all_lat)
    n_l = len(sorted_lat)
    p50 = sorted_lat[n_l // 2] if sorted_lat else 0
    p90 = sorted_lat[int(n_l * 0.90)] if n_l > 0 and int(n_l * 0.90) < n_l else (sorted_lat[-1] if sorted_lat else 0)
    p95 = sorted_lat[int(n_l * 0.95)] if n_l > 0 and int(n_l * 0.95) < n_l else (sorted_lat[-1] if sorted_lat else 0)
    p99 = sorted_lat[int(n_l * 0.99)] if n_l > 0 and int(n_l * 0.99) < n_l else (sorted_lat[-1] if sorted_lat else 0)
    log(f"  Latency: avg={avg_lat:.0f}ms  P50={p50:.0f}ms  P90={p90:.0f}ms  P95={p95:.0f}ms  P99={p99:.0f}ms")

    log(f"\n{_PAD}")
    log("DETAILED RESULTS (per query)")
    log(_PAD)
    for i, r in enumerate(results):
        if r.is_boundary:
            continue
        log(f"\n[{i + 1}] L{r.layer} \"{r.query[:50]}\"")
        if r.is_fallback:
            log(f"     FALLBACK (无有效检索结果)")
        else:
            log(f"     Faith={r.faithfulness:.0f}  Relev={r.answer_relevance:.0f}  "
                f"CP={r.context_precision:.3f}  CR={r.context_recall:.3f}")
            log(f"     Hit@1={r.hit_1} Hit@3={r.hit_3}  "
                f"Cite={r.valid_citations}/{r.total_citations}  "
                f"{r.latency_ms:.0f}ms")
            log(f"     Answer: {r.answer[:120]}...")
            log(f"     Retrieved: {r.context_docs}")
            log(f"     Expected:  {r.expected_docs}")

    return {
        "total_active": total_active,
        "total_all": total_all,
        "fallback_total": fallback_total,
        "all_faith": all_faith,
        "all_rel": all_rel,
        "all_cp": all_cp,
        "all_cr": all_cr,
        "all_hit1": all_hit1,
        "all_hit3": all_hit3,
        "all_hitk": all_hitk,
        "all_mrr": all_mrr,
        "all_ndcg": all_ndcg,
        "all_cit_t": all_cit_t,
        "all_cit_v": all_cit_v,
        "all_lat": all_lat,
    }


# ============================================================
# Main
# ============================================================

def serialize_results(results: list[RAGASResult]) -> list[dict]:
    out = []
    for r in results:
        out.append({
            "query": r.query,
            "expected_docs": r.expected_docs,
            "layer": r.layer,
            "is_boundary": r.is_boundary,
            "answer": r.answer[:500],
            "context_docs": r.context_docs,
            "retrieved_titles": r.retrieved_titles,
            "faithfulness": r.faithfulness,
            "answer_relevance": r.answer_relevance,
            "context_precision": r.context_precision,
            "context_recall": r.context_recall,
            "hit_1": r.hit_1, "hit_3": r.hit_3, "hit_k": r.hit_k,
            "mrr": r.mrr, "ndcg": r.ndcg,
            "total_citations": r.total_citations,
            "valid_citations": r.valid_citations,
            "citation_accuracy": r.citation_accuracy,
            "latency_ms": r.latency_ms,
            "retrieval_ms": r.retrieval_ms,
            "is_fallback": r.is_fallback,
        })
    return out


def main() -> None:
    # 只评测非边界 case
    normal_pairs = [qa for qa in QA_PAIRS if not qa.is_boundary]
    boundary_pairs = [qa for qa in QA_PAIRS if qa.is_boundary]

    log(f"""
╔══════════════════════════════════════════════════════════════════════╗
║                     RAG 系统 RAGAS 评测报告                           ║
║  Evaluator: {EVAL_MODEL_NAME:<50} ║
║  Time:      {time.strftime('%Y-%m-%d %H:%M:%S'):<50} ║
╚══════════════════════════════════════════════════════════════════════╝
""")
    log(f"Metrics: Faithfulness, Answer Relevance, Context Precision@K, Context Recall@K")
    log(f"Plus: Hit@K, MRR, NDCG@{RERANK_K}, Citation Accuracy, Latency")
    log(f"Total queries: {len(normal_pairs)} (non-boundary) + {len(boundary_pairs)} (boundary)")
    log(f"Recall K={RECALL_K}, Rerank K={RERANK_K}, rag_min_score={settings.rag_min_score}")

    # ──── 诊断：正常 QA 相似度分布（只检索不评分，快速扫一遍）────
    _diag_ckpt_path = Path(__file__).with_name("eval_checkpoint.json")
    _needs_diag = True
    if _diag_ckpt_path.exists():
        try:
            _diag_ckpt = json.loads(_diag_ckpt_path.read_text(encoding="utf-8"))
            _needs_diag = not any(r.get("max_similarity", 0) > 0 for r in _diag_ckpt.get("normal_results", []))
        except Exception:
            pass
    if _needs_diag:
        log("")
        log("=" * 70)
        log("DIAGNOSTIC: Normal QA similarity distribution (retrieval only)")
        import time as _time
        sims_normal = []
        sims_details = []
        for i, qa in enumerate(normal_pairs):
            hits = retrieve(qa.query)
            ms = max((h.get("milvus_sim", h.get("similarity", 0)) for h in hits), default=0.0)
            sims_normal.append(ms)
            sims_details.append((qa.query, qa.relevant_docs, qa.layer, ms))
            if (i + 1) % 20 == 0:
                log(f"  ... {i+1}/{len(normal_pairs)}")
            _time.sleep(0.15)
        sims_sorted = sorted(sims_normal)
        log(f"Normal QA (n={len(sims_normal)}): min={min(sims_normal):.4f} max={max(sims_normal):.4f} avg={sum(sims_normal)/len(sims_normal):.4f}")
        for t in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
            cnt = sum(1 for s in sims_normal if s < t)
            log(f"  Threshold {t}: kill {cnt}/{len(sims_normal)} ({cnt/len(sims_normal)*100:.1f}%)")
        log(f"  P5={sims_sorted[len(sims_sorted)//20]:.4f}  P10={sims_sorted[len(sims_sorted)//10]:.4f}")
        # border cases
        log("  Border zone (0.44-0.55):")
        for q, ed, l, s in sorted(sims_details, key=lambda x: x[3]):
            if 0.44 <= s <= 0.55:
                log(f"    {q:30s} layer={l} sim={s:.4f}  expected={ed}")
        log("=" * 70)
        log("")
    # ──── 诊断结束 ────

    log("")

    # 评测开始
    results: list[RAGASResult] = []
    total = len(normal_pairs)
    completed_queries: set[str] = set()

    # 断点续跑：从 checkpoint JSON 恢复
    CKPT_FILE = Path(__file__).with_name("eval_checkpoint.json")
    if CKPT_FILE.exists():
        try:
            ckpt = json.loads(CKPT_FILE.read_text(encoding="utf-8"))
            completed_queries = set(ckpt.get("done_queries", []))
            saved = ckpt.get("normal_results", [])
            for r_dict in saved:
                results.append(RAGASResult(**r_dict))
            log(f"  (断点续跑: 从 checkpoint 恢复 {len(results)} 条)")
        except Exception as e:
            log(f"  (checkpoint 解析失败: {e}, 重新开始)")
            results.clear()
            completed_queries.clear()

    def _save_checkpoint():
        data = {
            "done_queries": list(completed_queries),
            "normal_results": [r.__dict__ for r in results],
        }
        CKPT_FILE.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")

    for idx, qa in enumerate(normal_pairs):
        skipped = qa.query in completed_queries
        log(f"[{idx + 1:03d}/{total}] L{qa.layer} \"{qa.query[:50]}\""
            + (" (SKIP)" if skipped else ""))
        if skipped:
            continue
        try:
            r = evaluate_single(qa.query, qa.relevant_docs, qa.layer, qa.is_boundary)
            results.append(r)
            completed_queries.add(qa.query)
            _save_checkpoint()
            if r.is_fallback:
                log(f"     => FALLBACK (无有效检索结果, {r.latency_ms:.0f}ms)")
            else:
                log(f"     => F={r.faithfulness:.0f} R={r.answer_relevance:.0f} "
                    f"CP={r.context_precision:.3f} CR={r.context_recall:.3f} "
                    f"H@1={r.hit_1} {r.latency_ms:.0f}ms")
        except Exception as e:
            import traceback
            log(f"     => ERROR: {e}")
            log(f"        {traceback.format_exc()}")
            results.append(RAGASResult(
                query=qa.query,
                expected_docs=qa.relevant_docs,
                layer=qa.layer,
                is_boundary=qa.is_boundary,
                answer="(ERROR)", context_docs=[], context_texts=[],
                retrieved_titles=[],
                faithfulness=0, answer_relevance=0,
                context_precision=0, context_recall=0,
                hit_1=False, hit_3=False, hit_k=False,
                mrr=0, ndcg=0,
                total_citations=0, valid_citations=0, citation_accuracy=0,
                latency_ms=0, retrieval_ms=0, is_fallback=True,
            ))
            completed_queries.add(qa.query)
            _save_checkpoint()
        # 避免 API 限流
        time.sleep(0.3)

    # 边界评测 —— 拒答准确率
    boundary_results: list[RAGASResult] = []
    boundary_done: set[str] = set()

    # 断点续跑边界
    if CKPT_FILE.exists():
        try:
            ckpt = json.loads(CKPT_FILE.read_text(encoding="utf-8"))
            boundary_done = set(ckpt.get("boundary_done", []))
            saved_b = ckpt.get("boundary_results", [])
            for r_dict in saved_b:
                boundary_results.append(RAGASResult(**r_dict))
            if boundary_done:
                log(f"  (边界断点: 已恢复 {len(boundary_results)} 条)")
        except Exception:
            pass

    def _save_checkpoint_all():
        data = {
            "done_queries": list(completed_queries),
            "normal_results": [r.__dict__ for r in results],
            "boundary_done": list(boundary_done),
            "boundary_results": [r.__dict__ for r in boundary_results],
        }
        CKPT_FILE.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")

    # 先保存一下边界初始状态
    _save_checkpoint_all()

    log(f"\n{'>' * 56}")
    log("BOUNDARY (OUT-OF-DOMAIN) REJECTION TEST")
    log(f"{'>' * 56}")
    for idx, qa in enumerate(boundary_pairs):
        if qa.query in boundary_done:
            log(f"[B{idx + 1:02d}/{len(boundary_pairs)}] \"{qa.query[:50]}\" (SKIP)")
            continue
        log(f"[B{idx + 1:02d}/{len(boundary_pairs)}] \"{qa.query[:50]}\"")
        try:
            r = evaluate_single(qa.query, qa.relevant_docs, qa.layer, qa.is_boundary)
            boundary_results.append(r)
            boundary_done.add(qa.query)
            _save_checkpoint_all()
            label = "REJECTED ✓" if r.is_fallback else "FALSE POSITIVE ✗"
            top_title = r.retrieved_titles[0] if r.retrieved_titles else "N/A"
            log(f"     => {label} (max_sim={r.max_similarity:.4f}, top={top_title}, {r.latency_ms:.0f}ms)")
        except Exception as e:
            import traceback
            log(f"     => ERROR: {e}")
            log(f"        {traceback.format_exc()}")
            boundary_results.append(RAGASResult(
                query=qa.query,
                expected_docs=qa.relevant_docs,
                layer=qa.layer,
                is_boundary=qa.is_boundary,
                answer="(ERROR)", context_docs=[], context_texts=[],
                retrieved_titles=[],
                faithfulness=0, answer_relevance=0,
                context_precision=0, context_recall=0,
                hit_1=False, hit_3=False, hit_k=False,
                mrr=0, ndcg=0,
                total_citations=0, valid_citations=0, citation_accuracy=0,
                latency_ms=0, retrieval_ms=0, is_fallback=True,
            ))
            boundary_done.add(qa.query)
            _save_checkpoint_all()
        time.sleep(0.3)

    _save_checkpoint_all()

    boundary_total = len(boundary_results)
    boundary_rejected = sum(1 for r in boundary_results if r.is_fallback)
    boundary_pass = sum(1 for r in boundary_results if not r.is_fallback)
    rejection_rate = boundary_rejected / boundary_total * 100 if boundary_total else 0

    log(f"\n  边界测试总数: {boundary_total}")
    log(f"  正确拒答: {boundary_rejected} ({rejection_rate:.1f}%)")
    log(f"  误放行 (应拒未拒): {boundary_pass}")

    # 生成报告
    reports = build_layer_reports(results)
    summary = print_reports(reports, results)

    # 提取汇总指标
    total_active = summary["total_active"]
    all_faith = summary["all_faith"]
    all_rel = summary["all_rel"]
    all_hitk = summary["all_hitk"]
    all_mrr = summary["all_mrr"]
    all_lat = summary["all_lat"]
    sorted_lat = sorted(all_lat)
    n_l = len(sorted_lat)
    p95 = sorted_lat[int(n_l * 0.95)] if n_l > 0 and int(n_l * 0.95) < n_l else (sorted_lat[-1] if sorted_lat else 0)
    p99 = sorted_lat[int(n_l * 0.99)] if n_l > 0 and int(n_l * 0.99) < n_l else (sorted_lat[-1] if sorted_lat else 0)

    # 拒答准确率报告
    log(f"\n{_PAD}")
    log("OUT-OF-DOMAIN REJECTION (Boundary Cases)")
    log(_PAD)
    log(f"  rag_min_score (阈值): {settings.rag_min_score}")
    log(f"  边界测试总数: {boundary_total}")
    log(f"  正确拒答: {boundary_rejected} / {boundary_total} = {rejection_rate:.1f}%  ← 拒答准确率")
    log(f"  误放行: {boundary_pass} / {boundary_total} = {boundary_pass / boundary_total * 100:.1f}%" if boundary_total else "  误放行: N/A")
    log(f"  目标: ≥90%")
    log("")
    for i, r in enumerate(boundary_results):
        status = "✓ REJECTED" if r.is_fallback else "✗ PASSED (should reject)"
        log(f"  [{i + 1}] {status} \"{r.query[:60]}\"  ({r.latency_ms:.0f}ms)")

    # 保存结构化数据
    json_data = {
        "timestamp": int(time.time()),
        "config": {
            "eval_model": EVAL_MODEL_NAME,
            "recall_k": RECALL_K,
            "rerank_k": RERANK_K,
            "rag_min_score": settings.rag_min_score,
        },
        "results": serialize_results(results),
        "boundary": {
            "total": boundary_total,
            "rejected": boundary_rejected,
            "passed": boundary_pass,
            "rejection_rate": round(rejection_rate, 1),
            "items": serialize_results(boundary_results),
        },
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    log(f"\n报告已保存: {OUTPUT_TXT}")
    log(f"JSON 已保存: {OUTPUT_JSON}")
    log(f"\n  ╔══════════════════════════════════════════╗")
    log(f"  ║  Top-5命中率: {_rate(all_hitk, total_active):>6}              ║")
    log(f"  ║  MRR:       {all_mrr / total_active:.3f}" + " " * 30 + "║" if total_active else "  ║  MRR: N/A                              ║")
    log(f"  ║  Faithfulness: {_avg(all_faith)/5:.2f} (0-1 norm)       ║")
    log(f"  ║  Answer Relev: {_avg(all_rel)/5:.2f} (0-1 norm)       ║")
    log(f"  ║  拒答准确率:   {rejection_rate:.1f}%                ║")
    log(f"  ║  P95: {p95:.0f}ms  P99: {p99:.0f}ms                ║")
    log(f"  ╚══════════════════════════════════════════╝")


if __name__ == "__main__":
    main()