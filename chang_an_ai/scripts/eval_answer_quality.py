"""RAG 回答质量评估：忠实度 / 答案相关性 / 引用准确率 / 幻觉检测。

设计原则：
1. LLM-as-judge：用 DeepSeek 对回答打分，评估忠实度和答案相关性
2. 引用准确率程序化校验：正则提取 [n] 标注，验证 n 是否在有效范围内
3. 分层报告：按难度 Layer1-4 独立统计，定位回答质量短板
4. 数值化评分（1-5分）+ 人类可读明细，兼顾量化对比与可解释性

运行：python scripts/eval_answer_quality.py
输出：scripts/eval_answer_quality.txt
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm import chat_sync
from app.services.rag_service import answer as rag_answer, retrieve, _build_context
from scripts.eval_retrieval_full import QA_PAIRS

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
class AnswerQualityResult:
    query: str
    expected_docs: list[str]
    layer: int
    is_boundary: bool
    answer: str
    context_docs: list[str]
    context_texts: list[str]
    faithfulness: float           # 1-5 分，回答是否忠于上下文
    answer_relevance: float       # 1-5 分，回答是否切题
    citation_accuracy: float      # 0-1，[n] 引用中有效的比例
    total_citations: int          # 回答中 [n] 总数
    valid_citations: int          # 有效的 [n] 数量
    latency_ms: float


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
# 评估函数
# ============================================================

def _call_judge(prompt: str) -> float:
    """调用 LLM 打分，返回 1-5 的浮点数。"""
    try:
        resp = chat_sync(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=60.0,
        )
        match = re.search(r"([1-5])", resp.strip())
        if match:
            return float(match.group(1))
        return 0.0
    except Exception as e:
        log(f"    Judge error: {e}")
        return 0.0


def _check_citations(answer: str, context_count: int) -> tuple[int, int]:
    """检查回答中 [n] 引用标注的有效性。

    提取所有 [数字] 模式，统计总数和有效数（n 在 1~context_count 范围内）。

    Returns:
        (total_citations, valid_citations)
    """
    citations = re.findall(r"\[(\d+)\]", answer)
    if not citations:
        return 0, 0
    total = len(citations)
    valid = sum(1 for c in citations if 1 <= int(c) <= context_count)
    return total, valid


def evaluate_single(query: str, expected_docs: list[str], layer: int,
                    is_boundary: bool) -> AnswerQualityResult:
    """对单个 query 执行完整 RAG 流程并评估回答质量。"""
    t0 = time.perf_counter()

    # 先检索获取完整上下文（不走 sources 事件里的截断版）
    from app.services.query_rewrite import rewrite
    search_query = rewrite(query, [])
    hits = retrieve(search_query)
    from app.config import settings
    valid_hits = [h for h in hits if h.get("milvus_sim", h.get("similarity", 0)) >= settings.rag_min_score]
    full_context = _build_context(valid_hits) if valid_hits else ""

    # 调用 RAG 管道获取回答
    events = list(rag_answer(query, []))
    answer = ""
    context_docs = []
    sources_count = 0

    for event_type, payload in events:
        if event_type == "sources":
            sources_count = len(payload) if payload else 0
            context_docs = [s.get("title", "") for s in payload] if payload else []
        elif event_type == "delta":
            answer += payload if isinstance(payload, str) else ""

    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000

    # 如果无有效上下文（被阈值拦截），直接返回
    if sources_count == 0 or not answer.strip():
        return AnswerQualityResult(
            query=query,
            expected_docs=expected_docs,
            layer=layer,
            is_boundary=is_boundary,
            answer=answer.strip() or "(FALLBACK — 无有效检索结果)",
            context_docs=[],
            context_texts=[],
            faithfulness=0.0,
            answer_relevance=0.0,
            citation_accuracy=0.0,
            total_citations=0,
            valid_citations=0,
            latency_ms=latency_ms,
        )

    # LLM-as-judge: 忠实度（用完整上下文，不是 sources 事件的 80 字符截断版）
    faith_prompt = FAITHFULNESS_PROMPT.format(
        context=full_context[:6000],
        question=query,
        answer=answer,
    )
    faithfulness = _call_judge(faith_prompt)

    # LLM-as-judge: 答案相关性
    rel_prompt = RELEVANCE_PROMPT.format(
        question=query,
        answer=answer,
    )
    answer_relevance = _call_judge(rel_prompt)

    # 引用准确率
    total_cit, valid_cit = _check_citations(answer, sources_count)
    citation_accuracy = valid_cit / total_cit if total_cit > 0 else 0.0

    return AnswerQualityResult(
        query=query,
        expected_docs=expected_docs,
        layer=layer,
        is_boundary=is_boundary,
        answer=answer,
        context_docs=context_docs,
        context_texts=[full_context],
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
        citation_accuracy=citation_accuracy,
        total_citations=total_cit,
        valid_citations=valid_cit,
        latency_ms=latency_ms,
    )


# ============================================================
# Main
# ============================================================
def main() -> None:
    # 只评估非边界 case
    normal_pairs = [qa for qa in QA_PAIRS if not qa.is_boundary]
    log(_PAD)
    log("RAG ANSWER QUALITY EVALUATION — LLM-as-Judge + Citation Check")
    log(_PAD)
    log(f"Total non-boundary queries: {len(normal_pairs)}")
    log(f"Judge model: DeepSeek (via chat_sync, temperature=0.1)")
    log("")

    results: list[AnswerQualityResult] = []

    for idx, qa in enumerate(normal_pairs):
        log(f"[{idx + 1:03d}/{len(normal_pairs)}] L{qa.layer} '{qa.query[:40]}'")
        try:
            r = evaluate_single(
                qa.query, qa.relevant_docs, qa.layer, qa.is_boundary
            )
            results.append(r)
            log(f"    Faith={r.faithfulness:.0f}  Relev={r.answer_relevance:.0f}  "
                f"Cite={r.valid_citations}/{r.total_citations} "
                f"({r.citation_accuracy:.0%})  {r.latency_ms:.0f}ms")
            log(f"    Answer: {r.answer[:100]}...")
        except Exception as e:
            import traceback
            log(f"    ERROR: {e}")
            log(f"    Traceback: {traceback.format_exc()}")
        # 避免 API 限流：每条间隔 0.5s
        time.sleep(0.3)

    # ============================================================
    # 分层统计
    # ============================================================
    layer_map: dict[int, list[AnswerQualityResult]] = defaultdict(list)
    for r in results:
        layer_map[r.layer].append(r)

    layer_names = {1: "Core Facts", 2: "Basic", 3: "Advanced", 4: "Robustness"}

    log(f"\n{_PAD}")
    log("PER-LAYER ANSWER QUALITY REPORT")
    log(_PAD)

    for layer in [1, 2, 3, 4]:
        items = layer_map.get(layer, [])
        if not items:
            continue

        faith_scores = [r.faithfulness for r in items if r.faithfulness > 0]
        rel_scores = [r.answer_relevance for r in items if r.answer_relevance > 0]
        cite_accs = [r.citation_accuracy for r in items if r.total_citations > 0]
        total_cits = sum(r.total_citations for r in items)
        valid_cits = sum(r.valid_citations for r in items)

        avg_faith = sum(faith_scores) / len(faith_scores) if faith_scores else 0
        avg_rel = sum(rel_scores) / len(rel_scores) if rel_scores else 0
        avg_cite = sum(cite_accs) / len(cite_accs) if cite_accs else 0
        overall_cite = valid_cits / total_cits if total_cits > 0 else 0

        log(f"\n{'─' * 48}")
        log(f"Layer {layer}: {layer_names[layer]} ({len(items)} queries)")
        log(f"{'─' * 48}")
        log(f"  Faithfulness:       {avg_faith:.1f}/5  (目标 ≥4.0)")
        log(f"  Answer Relevance:   {avg_rel:.1f}/5  (目标 ≥4.0)")
        log(f"  Citation Accuracy:  {overall_cite:.1%}  ({valid_cits}/{total_cits} valid, 目标 ≥80%)")

        # 幻觉统计
        hallucinated = sum(1 for r in items if 0 < r.faithfulness <= 2)
        partial = sum(1 for r in items if 2 < r.faithfulness <= 3)
        good = sum(1 for r in items if r.faithfulness > 3)
        fallback = sum(1 for r in items if r.faithfulness == 0)
        log(f"  Faith distribution: HALLUC={hallucinated} PARTIAL={partial} GOOD={good} FALLBACK={fallback}")

    # ============================================================
    # 汇总
    # ============================================================
    all_faith = [r.faithfulness for r in results if r.faithfulness > 0]
    all_rel = [r.answer_relevance for r in results if r.answer_relevance > 0]
    all_cite_accs = [r.citation_accuracy for r in results if r.total_citations > 0]
    total_all_cits = sum(r.total_citations for r in results)
    valid_all_cits = sum(r.valid_citations for r in results)
    all_lat = [r.latency_ms for r in results]

    avg_f = sum(all_faith) / len(all_faith) if all_faith else 0
    avg_r = sum(all_rel) / len(all_rel) if all_rel else 0
    avg_c = sum(all_cite_accs) / len(all_cite_accs) if all_cite_accs else 0
    overall_c = valid_all_cits / total_all_cits if total_all_cits > 0 else 0
    sorted_lat = sorted(all_lat)

    log(f"\n{_PAD}")
    log("OVERALL ANSWER QUALITY SUMMARY")
    log(_PAD)
    log(f"  Total evaluated:    {len(results)} queries")
    log(f"  Faithfulness:       {avg_f:.1f}/5  (目标 ≥4.0)")
    log(f"  Answer Relevance:   {avg_r:.1f}/5  (目标 ≥4.0)")
    log(f"  Citation Accuracy:  {overall_c:.1%}  ({valid_all_cits}/{total_all_cits} valid, 目标 ≥80%)")
    log(f"  Latency:            p50={sorted_lat[len(sorted_lat)//2]:.0f}ms  "
        f"p95={sorted_lat[int(len(sorted_lat)*0.95)]:.0f}ms")

    # 幻觉率
    hallucinated = sum(1 for r in results if 0 < r.faithfulness <= 2)
    partial = sum(1 for r in results if 2 < r.faithfulness <= 3)
    good = sum(1 for r in results if r.faithfulness > 3)
    fallback = sum(1 for r in results if r.faithfulness == 0)
    total_eval = len(results)
    log(f"\n  Hallucination Report:")
    log(f"    Hallucinated (1-2):  {hallucinated}/{total_eval} = {hallucinated/total_eval:.1%}")
    log(f"    Partial (3):         {partial}/{total_eval} = {partial/total_eval:.1%}")
    log(f"    Good (4-5):          {good}/{total_eval} = {good/total_eval:.1%}")
    log(f"    Fallback (no ctx):   {fallback}/{total_eval} = {fallback/total_eval:.1%}")
    halluc_rate = hallucinated / total_eval if total_eval > 0 else 0
    log(f"    Hallucination Rate:  {halluc_rate:.1%} (目标 ≤5%)")

    # ============================================================
    # 逐条明细
    # ============================================================
    log(f"\n{_PAD}")
    log("DETAILED PER-QUERY RESULTS")
    log(_PAD)
    for r in results:
        faith_label = "HALLUC" if 0 < r.faithfulness <= 2 else ("PARTIAL" if r.faithfulness <= 3 else ("GOOD" if r.faithfulness > 3 else "FALLBACK"))
        log(f"  [{faith_label:>7}] L{r.layer} '{r.query[:35]}'")
        log(f"    Faith={r.faithfulness:.0f} Relev={r.answer_relevance:.0f} "
            f"Cite={r.valid_citations}/{r.total_citations} "
            f"({r.latency_ms:.0f}ms)")
        log(f"    Answer: {r.answer[:120]}")
        if r.context_docs:
            log(f"    Context: {r.context_docs[:3]}")

    log(f"\n{_PAD}")
    log("EVALUATION COMPLETE")
    log(f"Report: {OUTPUT}")
    log(_PAD)


if __name__ == "__main__":
    main()