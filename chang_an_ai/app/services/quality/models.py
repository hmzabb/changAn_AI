"""质量评估数据模型（格式无关）。

所有格式（.md / .pdf / .docx）共用同一套数据结构，
保证写入向量库的 metadata 结构一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChunkQuality:
    """单个 chunk 的质量评估结果（格式无关）。

    不论 chunk 来自 .md 按标题切分、.pdf 按段落切分还是 .docx，
    都用同一套字段表达质量。
    """

    text_sample: str           # 文本前 80 字符（日志/审核用，不存全文）
    text_health_score: float   # 文本健康度 0-1（长度、乱码、空值）
    has_tables: bool = False   # 是否检测到表格内容
    table_score: float | None = None   # 表格完整性分（无表时为 None）
    overall_score: float = 0.0
    issues: list[str] = field(default_factory=list)
    suggestion: str = "direct_insert"


@dataclass
class TableQuality:
    """单个表格的质量评估（被 ChunkQuality 引用）。"""

    structural_score: float
    semantic_score: float
    consistency_score: float
    overall_score: float
    issues: list[str] = field(default_factory=list)
    suggestion: str = "direct_insert"
    final_text: str = ""
    page_num: int = 0
    table_index: int = 0
    row_count: int = 0
    col_count: int = 0


@dataclass
class FileQualityReport:
    """单个文件的质量报告（聚合该文件所有 chunk 的评估结果）。

    格式无关：.md / .pdf / .docx 都用同一个报告结构。
    """

    file_name: str
    file_format: str           # "md" / "pdf" / "docx"
    total_chunks: int = 0
    total_tables: int = 0
    passed: int = 0
    llm_fixed: int = 0
    needs_review: int = 0
    chunk_qualities: list[ChunkQuality] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)