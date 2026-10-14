"""PDF 文件处理入口（向后兼容）。

模块职责：
  - process_pdf_with_quality: PDF 提取 → 段落切分 → 统一质量评估

提取逻辑在 readers.py 中（_extract_pdf），此处委托调用，保留此入口以兼容现有调用方。
"""
from __future__ import annotations

from pathlib import Path

from app.services.quality.models import FileQualityReport


def process_pdf_with_quality(file_path: Path) -> tuple[str, FileQualityReport]:
    """PDF 文件处理：提取 → 评估 → 返回聚合文本和报告。

    保留此函数以兼容现有调用方，内部委托给 _extract_pdf + 统一管线。

    Args:
        file_path: PDF 文件路径

    Returns:
        (aggregated_text, report)
    """
    from app.services.quality.assessor import assess_file_quality
    from app.services.quality.readers import _extract_pdf

    aggregated = _extract_pdf(file_path)

    chunk_texts = [p for p in aggregated.split("\n\n") if p.strip()]
    report = assess_file_quality(
        file_name=file_path.name,
        file_format="pdf",
        chunks_texts=chunk_texts,
    )

    return aggregated, report