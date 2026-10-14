"""文档质量评估包（格式无关的三段式质量门）。

包结构（按职责拆分）:
    config.py       — 全局配置常量（阈值、路径、超时）
    models.py       — 数据模型（ChunkQuality / TableQuality / FileQualityReport）
    scoring.py      — 评分函数（文本健康度 + 表格完整性）
    assessor.py     — 统一评估入口（chunk + 文件级别）
    remediator.py   — LLM 降级修复
    review.py       — 审核队列持久化
    pdf_processor.py — PDF 专用入口（向后兼容）
    legacy.py       — 旧版表格 API（向后兼容）
    readers.py      — 格式无关文档读取（简单 dict 映射，零类零装饰器）

公共 API（建议新代码从 app.services.quality 直接导入）:
    - assess_chunk_quality, assess_file_quality
    - remediate_chunk
    - process_pdf_with_quality
    - read_document（统一文档读取入口）
    - ChunkQuality, TableQuality, FileQualityReport
"""
from app.services.quality.config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, REVIEW_QUEUE_DIR
from app.services.quality.models import ChunkQuality, FileQualityReport, TableQuality
from app.services.quality.scoring import (
    _detect_tables_in_text,
    _score_table_in_chunk,
    _score_text_health,
)
from app.services.quality.assessor import assess_chunk_quality, assess_file_quality
from app.services.quality.remediator import _get_fix_client, _llm_fix_chunk, remediate_chunk
from app.services.quality.pdf_processor import process_pdf_with_quality
from app.services.quality.legacy import (
    _clean_cell,
    _extract_table_from_raw,
    _handle_table_fix,
    _rows_to_text,
    _score_consistency,
    _score_semantic,
    _score_structural,
    assess_table_quality,
)

__all__ = [
    # 配置
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "REVIEW_QUEUE_DIR",
    # 数据模型
    "ChunkQuality",
    "FileQualityReport",
    "TableQuality",
    # 核心评估
    "assess_chunk_quality",
    "assess_file_quality",
    # 降级修复
    "remediate_chunk",
    # PDF 入口
    "process_pdf_with_quality",
    # 表格专用（向后兼容）
    "assess_table_quality",
    # 内部函数（测试需要）
    "_clean_cell",
    "_detect_tables_in_text",
    "_extract_table_from_raw",
    "_get_fix_client",
    "_handle_table_fix",
    "_llm_fix_chunk",
    "_score_semantic",
    "_score_structural",
    "_score_table_in_chunk",
    "_score_text_health",
]