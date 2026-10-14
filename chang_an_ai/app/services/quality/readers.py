"""格式无关的文档读取层 — 轻量版。

设计思路：
  - 不引入 ABC 基类、装饰器、注册表等抽象
  - 用一个 dict 做扩展名→(提取函数, 分块策略) 映射
  - 新增格式：在 _READERS 字典加一行 + 写一个提取函数即可

复杂度对比：
  - 旧版（if-else 散落在 ingest_service）：加格式改 3 处
  - 新版（这个文件）：加格式改 1 处（仅在 _READERS 加一行）
  三格式场景下已经够用，不再引入策略模式重型装备。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


# ============================================================================
# 提取函数（纯函数，无类，可独立测试）
# ============================================================================

def _extract_markdown(file_path: Path) -> str:
    """Markdown：直接读 UTF-8 文本。"""
    return file_path.read_text(encoding="utf-8")


def _extract_pdf(file_path: Path) -> str:
    """PDF：pdfplumber 提取文本 + 表格转管道符格式。"""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("请安装 pdfplumber：pip install pdfplumber>=0.11.0")

    text_parts: list[str] = []
    with pdfplumber.open(str(file_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text and page_text.strip():
                text_parts.append(page_text.strip())

            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                lines = []
                for row in table:
                    cells = [str(c) if c is not None else "" for c in row]
                    lines.append(" | ".join(cells))
                if lines:
                    text_parts.append("\n".join(lines))

    return "\n\n".join(text_parts)


def _extract_docx(file_path: Path) -> str:
    """DOCX：python-docx 提取段落 + 表格转管道符格式。"""
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("请安装 python-docx：pip install python-docx>=1.1.0")

    doc = Document(str(file_path))
    parts: list[str] = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    for table in doc.tables:
        lines = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append(" | ".join(cells))
        if lines:
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ============================================================================
# 映射表：扩展名 → (提取函数, 分块策略)
# ============================================================================

_READERS: dict[str, tuple[Callable[[Path], str], str]] = {
    ".md": (_extract_markdown, "markdown"),
    ".pdf": (_extract_pdf, "plain"),
    ".docx": (_extract_docx, "plain"),
}


def extract_text(file_path: Path | str) -> str:
    """统一文本提取入口。

    根据文件后缀自动选择提取函数，不支持的格式抛出 ValueError。
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    entry = _READERS.get(ext)
    if entry is None:
        raise ValueError(
            f"不支持的文档格式：{ext}，当前支持：{', '.join(sorted(_READERS))}"
        )
    extract_func, _ = entry
    return extract_func(path)


def chunk_strategy(file_path: Path | str) -> str:
    """返回该文件的分块策略标识："markdown" 或 "plain"。"""
    ext = Path(file_path).suffix.lower()
    entry = _READERS.get(ext)
    if entry is None:
        raise ValueError(f"不支持的文档格式：{ext}")
    _, strategy = entry
    return strategy


def supported_extensions() -> list[str]:
    """返回所有支持的扩展名列表。"""
    return list(_READERS.keys())


__all__ = ["extract_text", "chunk_strategy", "supported_extensions"]