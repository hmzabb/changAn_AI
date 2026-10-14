"""审核队列持久化模块。

模块职责：
  - _write_chunk_review_queue: 将文件中需审核的 chunk 写入 JSONL 队列
  - _write_chunk_review_queue_single: 将单个低质量表格写入审核队列（向后兼容）

依赖：config 常量、models 数据结构、JSON 标准库。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from app.services.quality.config import REVIEW_QUEUE_DIR
from app.services.quality.models import FileQualityReport, TableQuality

logger = logging.getLogger(__name__)


def _write_chunk_review_queue(report: FileQualityReport) -> None:
    """将文件中需审核的 chunk 写入 JSONL 队列。"""
    queue_path = REVIEW_QUEUE_DIR / f"{Path(report.file_name).stem}_review.jsonl"
    try:
        with open(queue_path, "a", encoding="utf-8") as f:
            for i, cq in enumerate(report.chunk_qualities):
                if cq.suggestion != "human_review":
                    continue
                record = {
                    "file": report.file_name,
                    "format": report.file_format,
                    "chunk_index": i,
                    "overall_score": cq.overall_score,
                    "text_health": cq.text_health_score,
                    "has_tables": cq.has_tables,
                    "table_score": cq.table_score,
                    "issues": cq.issues,
                    "sample": cq.text_sample,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.warning(
            "%d 个 chunk 已加入审核队列：%s",
            report.needs_review, queue_path.name,
        )
    except OSError as exc:
        logger.error("写入审核队列失败：%s", exc)


def _write_chunk_review_queue_single(quality: TableQuality, file_name: str) -> None:
    """将单个低质量表格写入审核队列（向后兼容）。"""
    queue_path = REVIEW_QUEUE_DIR / f"{Path(file_name).stem}_review.jsonl"
    record = {
        "file": file_name,
        "page": quality.page_num,
        "table_index": quality.table_index,
        "overall_score": quality.overall_score,
        "structural_score": quality.structural_score,
        "semantic_score": quality.semantic_score,
        "issues": quality.issues,
        "row_count": quality.row_count,
        "col_count": quality.col_count,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.warning(
            "表格已加入审核队列：%s P%d T%d (score=%.2f)",
            file_name, quality.page_num, quality.table_index, quality.overall_score,
        )
    except OSError as exc:
        logger.error("写入审核队列失败：%s", exc)