"""向后兼容模块。

所有实现已迁移到 app.services.quality 子包（按职责拆分）。
此文件仅做重导出，保证现有调用方（ingest_service、tests）无需修改。

如需新增功能，请直接在 app/services/quality/ 下对应模块中添加。
"""
from app.services.quality import (  # noqa: F401  — 重导出，所有符号供外部使用
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    REVIEW_QUEUE_DIR,
    ChunkQuality,
    FileQualityReport,
    TableQuality,
    _clean_cell,
    _detect_tables_in_text,
    _extract_table_from_raw,
    _get_fix_client,
    _handle_table_fix,
    _llm_fix_chunk,
    _score_semantic,
    _score_structural,
    _score_table_in_chunk,
    _score_text_health,
    assess_chunk_quality,
    assess_file_quality,
    assess_table_quality,
    process_pdf_with_quality,
    remediate_chunk,
)