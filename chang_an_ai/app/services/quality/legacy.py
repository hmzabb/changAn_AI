"""向后兼容：表格专用评分与修复函数。

模块职责：
  - _clean_cell, _extract_table_from_raw, _rows_to_text: 表格数据清洗工具
  - _score_structural, _score_semantic, _score_consistency: 表格三维度评分
  - assess_table_quality: 旧版表格评估入口（新代码应使用 assess_chunk_quality）
  - _handle_table_fix: 旧版表格修复路由

保留此模块以兼容已有测试和 PDF 专用场景。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.quality.config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
from app.services.quality.models import TableQuality
from app.services.quality.remediator import _llm_fix_chunk
from app.services.quality.review import _write_chunk_review_queue_single

logger = logging.getLogger(__name__)


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s*\n\s*", " ", str(value).strip())


def _extract_table_from_raw(raw_table: list[list[Any]]) -> tuple[list[str], list[list[str]]]:
    cleaned = [[_clean_cell(c) for c in row] for row in raw_table]
    header_idx = 0
    for i, row in enumerate(cleaned):
        if any(c for c in row):
            header_idx = i
            break
    else:
        return [], []
    headers = cleaned[header_idx]
    rows = [row for row in cleaned[header_idx + 1:] if any(c for c in row)]
    return headers, rows


def _rows_to_text(headers: list[str], rows: list[list[str]]) -> str:
    lines = [" | ".join(headers)]
    for row in rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def _score_structural(headers: list[str], rows: list[list[str]]) -> tuple[float, list[str]]:
    issues: list[str] = []
    score = 1.0
    col_counts = [len(row) for row in rows]
    unique_cols = set(col_counts)
    if len(unique_cols) > 1:
        penalty = min(0.4, 0.08 * len(unique_cols))
        score -= penalty
        issues.append(f"列数不一致：出现 {len(unique_cols)} 种列数 {sorted(unique_cols)}")
    total_cells = sum(len(row) for row in rows)
    empty_cells = sum(1 for row in rows for c in row if not c)
    if total_cells > 0:
        empty_rate = empty_cells / total_cells
        if empty_rate > 0.5:
            score -= 0.3
            issues.append(f"空单元格率过高：{empty_rate:.0%}")
        elif empty_rate > 0.3:
            score -= 0.15
            issues.append(f"空单元格率偏高：{empty_rate:.0%}")
    if len(headers) < 2:
        score -= 0.2
        issues.append("表头列数 < 2，可能不是有效表格或提取损坏")
    if len(rows) == 0:
        score -= 0.4
        issues.append("无数据行，空表")
    return max(0.0, round(score, 4)), issues


def _score_semantic(headers: list[str], rows: list[list[str]]) -> tuple[float, list[str]]:
    issues: list[str] = []
    header_lens = [len(h) for h in headers if h]
    if header_lens:
        avg_header_len = sum(header_lens) / len(header_lens)
        if avg_header_len < 1.5:
            issues.append("表头过短，可能为空或乱码")
        elif avg_header_len > 30:
            issues.append("表头过长，可能误判大段文本为表格")
    else:
        return 0.0, ["无有效表头"]

    header_score = 0.5 if issues else 1.0
    all_cells = [c for row in rows for c in row]
    numeric_cells = sum(1 for c in all_cells if any(ch.isdigit() for ch in c))
    numeric_ratio = numeric_cells / max(len(all_cells), 1)
    if numeric_ratio >= 0.15:
        numeric_score = 1.0
    elif numeric_ratio >= 0.05:
        numeric_score = 0.7
    else:
        numeric_score = 0.4
        if len(rows) > 3:
            issues.append(f"数值含量极低（{numeric_ratio:.0%}），可能不是结构化数据表")

    garbage_pattern = re.compile(r"[^\x20-\x7E\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{3,}")
    garbage_rows = sum(1 for row in rows for c in row if garbage_pattern.search(c))
    garbage_score = 0.5 if garbage_rows > len(all_cells) * 0.1 else 1.0
    if garbage_score < 1.0:
        issues.append(f"检测到乱码字符（{garbage_rows} 个单元格）")

    score = 0.4 * header_score + 0.35 * numeric_score + 0.25 * garbage_score
    return round(score, 4), issues


def _score_consistency(
    headers: list[str],
    rows: list[list[str]],
    _prev_headers: list[str] | None = None,
) -> tuple[float, list[str]]:
    return 1.0, []


def assess_table_quality(
    headers: list[str],
    rows: list[list[str]],
    *,
    page_num: int = 0,
    table_index: int = 0,
    prev_headers: list[str] | None = None,
) -> TableQuality:
    """对单个原始表格（表头+数据行）做三维度置信度评估。

    保留此函数以兼容已有测试和 PDF 专用场景。
    新代码应优先使用 assess_chunk_quality（统一 chunk 级别评估）。
    """
    if not headers or not rows:
        return TableQuality(
            structural_score=0.0, semantic_score=0.0, consistency_score=0.0,
            overall_score=0.0, issues=["空表格，无有效数据"],
            suggestion="human_review", page_num=page_num, table_index=table_index,
            row_count=len(rows), col_count=len(headers),
        )
    structural, s_issues = _score_structural(headers, rows)
    semantic, m_issues = _score_semantic(headers, rows)
    consistency, c_issues = _score_consistency(headers, rows, prev_headers)
    overall = round(0.40 * structural + 0.35 * semantic + 0.25 * consistency, 4)
    all_issues = s_issues + m_issues + c_issues
    if overall >= CONFIDENCE_HIGH:
        suggestion = "direct_insert"
    elif overall >= CONFIDENCE_MEDIUM:
        suggestion = "llm_fix"
    else:
        suggestion = "human_review"
    return TableQuality(
        structural_score=structural, semantic_score=semantic,
        consistency_score=consistency, overall_score=overall,
        issues=all_issues, suggestion=suggestion,
        page_num=page_num, table_index=table_index,
        row_count=len(rows), col_count=len(headers),
    )


def _handle_table_fix(
    headers: list[str],
    rows: list[list[str]],
    quality: TableQuality,
    file_name: str,
) -> str:
    """向后兼容：表格修复函数。"""
    raw_text = _rows_to_text(headers, rows)
    if quality.suggestion == "direct_insert":
        return raw_text
    if quality.suggestion == "llm_fix":
        return _llm_fix_chunk(raw_text, quality.issues)
    _write_chunk_review_queue_single(quality, file_name)
    return raw_text