"""统一质量评估：chunk 级别 + 文件级别。

模块职责：
  - assess_chunk_quality: 格式无关的 chunk 质量评估（唯一对外入口）
  - assess_file_quality: 聚合所有 chunk 的评估报告

依赖关系：scoring（评分函数）→ models（数据结构）→ config（阈值常量）
"""
from __future__ import annotations

import logging

from app.services.quality.config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from app.services.quality.models import ChunkQuality, FileQualityReport
from app.services.quality.scoring import (
    _detect_tables_in_text,
    _score_table_in_chunk,
    _score_text_health,
)

logger = logging.getLogger(__name__)


def assess_chunk_quality(text: str) -> ChunkQuality:
    """统一 chunk 质量评估（格式无关入口）。

    这是整个质量模块唯一对外的评估入口。
    无论 chunk 来自什么格式，都走同样的逻辑：
      1. 文本健康度（必检，权重 60%）
      2. 表格完整性（检测到表格才检，权重 40%）

    Args:
        text: chunk 文本内容

    Returns:
        ChunkQuality，包含评分和建议策略。
    """
    text_health, health_issues = _score_text_health(text)
    has_tables = _detect_tables_in_text(text)

    table_score: float | None = None
    table_issues: list[str] = []

    if has_tables:
        table_score, table_issues = _score_table_in_chunk(text)
        overall = round(0.60 * text_health + 0.40 * table_score, 4)
    else:
        overall = text_health

    all_issues = health_issues + table_issues

    if overall >= CONFIDENCE_HIGH:
        suggestion = "direct_insert"
    elif overall >= CONFIDENCE_MEDIUM:
        suggestion = "llm_fix"
    else:
        suggestion = "human_review"

    return ChunkQuality(
        text_sample=text[:80].replace("\n", " "),
        text_health_score=text_health,
        has_tables=has_tables,
        table_score=table_score,
        overall_score=overall,
        issues=all_issues,
        suggestion=suggestion,
    )


def assess_file_quality(
    file_name: str,
    file_format: str,
    chunks_texts: list[str],
) -> FileQualityReport:
    """对单个文件的所有 chunk 做批量质量评估，返回聚合报告。

    支持所有格式：
      - .md 文件：chunks_texts 来自 chunk_markdown 的输出
      - .pdf/.docx 文件：chunks_texts 来自 chunk_plain_text 的输出

    Args:
        file_name: 文件名（含扩展名）
        file_format: 格式标识 "md" / "pdf" / "docx"
        chunks_texts: 该文件切分后的所有 chunk 文本列表

    Returns:
        FileQualityReport，按格式记录，统计 passed/llm_fixed/needs_review。
    """
    # 延迟导入 review 模块避免循环依赖
    from app.services.quality.review import _write_chunk_review_queue

    report = FileQualityReport(
        file_name=file_name,
        file_format=file_format,
        total_chunks=len(chunks_texts),
    )

    for text in chunks_texts:
        cq = assess_chunk_quality(text)
        report.chunk_qualities.append(cq)

        if cq.has_tables:
            report.total_tables += 1

        if cq.suggestion == "direct_insert":
            report.passed += 1
        elif cq.suggestion == "llm_fix":
            report.llm_fixed += 1
        else:
            report.needs_review += 1
            report.errors.append(
                f"Chunk #{report.total_chunks} 需审核（score={cq.overall_score}）："
                + "；".join(cq.issues)
            )

    if report.needs_review > 0:
        _write_chunk_review_queue(report)

    logger.info(
        "文档质量报告 [%s]：%d chunks，通过 %d，LLM修复 %d，待审核 %d（检测到 %d 个表格）",
        file_name, report.total_chunks,
        report.passed, report.llm_fixed, report.needs_review,
        report.total_tables,
    )

    return report