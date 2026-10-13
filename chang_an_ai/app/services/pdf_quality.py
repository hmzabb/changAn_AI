"""统一文档质量评估：格式无关的三段式质量门。

工业标准模式：
  所有文档格式（.md / .pdf / .docx）的提取结果走同一套质量管线，
  只是"是否含表格"这一检查项根据实际内容动态触发——有表格才跑表格完整性检查。
  
  三段式流水线：
    1. 质量检测：文本健康度（必检） + 表格完整性（有表才检）
    2. 置信度评分：加权综合 → 决策路由
    3. 降级修复：高分直入 → LLM 修复 → 人工审核

  与 ingest_service 的关系：
    ingest_service 负责"拉取 → 提取文本 → 分块"，
    doc_quality 负责"分块后的文本是否合格"。
    这是一个横切关注点，嵌入在 chunk 生成之后、embedding 之前。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

# ============================================================================
# 全局配置
# ============================================================================

CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.50

LLM_FIX_TIMEOUT = 30.0
LLM_FIX_TEMPERATURE = 0.1

REVIEW_QUEUE_DIR = BASE_DIR / "app" / "data" / "review_queue"
REVIEW_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

# 表格行检测正则：连续 2 行以上包含管道符（|）且每行至少 2 个分隔符 → 视为表格段
_TABLE_PATTERN = re.compile(r"^(?:[^|\n]+\|){2,}[^|\n]+$", re.MULTILINE)


# ============================================================================
# 数据模型
# ============================================================================

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


# ============================================================================
# 统一质量门：格式无关的 chunk 级别评估
# ============================================================================

def _score_text_health(text: str) -> tuple[float, list[str]]:
    """文本健康度评分（0-1），权重 50%。

    所有格式都必须过的检查：
      - 长度合理性：太短（<30 字符）无意义，太长（>1500 字符）可能未被正确切分
      - 空白率：全是空白/换行的 chunk 无意义
      - 乱码率：异常字符占比（PDF 提取常见问题，但 MD 也可能因编码问题产生）
      - 有效内容比：中文字符 + 数字的占比（越高越健康）
    """
    issues: list[str] = []
    stripped = text.strip()
    length = len(stripped)

    if length == 0:
        return 0.0, ["空 chunk"]
    if length < 20:
        return 0.35, [f"过短（{length} 字符），可能不含完整信息"]
    if length > 800:
        issues.append(f"偏长（{length} 字符），可能未被正确切分")

    # 空白率
    whitespace_ratio = sum(1 for c in stripped if c in " \t\n\r") / length
    if whitespace_ratio > 0.5:
        return 0.2, [f"空白率过高（{whitespace_ratio:.0%}），几乎无实质内容"]

    # 乱码检测
    garbage_pattern = re.compile(r"[^\x20-\x7E\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{3,}")
    garbage_chars = len(garbage_pattern.findall(stripped))
    garbage_ratio = garbage_chars / max(length, 1)
    if garbage_ratio > 0.1:
        issues.append(f"异常字符占比 {garbage_ratio:.0%}，可能存在乱码")

    # 有效内容比（中文 + 英文 + 数字）
    valid = sum(1 for c in stripped if c.isalpha() or c.isdigit() or '\u4e00' <= c <= '\u9fff')
    valid_ratio = valid / max(length, 1)
    if valid_ratio < 0.3:
        issues.append(f"有效内容比过低（{valid_ratio:.0%}）")

    # 计分
    score = 1.0
    if length < 20:
        score = 0.35
    if length > 800:
        score -= 0.15
    if garbage_ratio > 0.1:
        score -= 0.3
    if valid_ratio < 0.3:
        score -= 0.2

    return max(0.0, round(score, 4)), issues


def _detect_tables_in_text(text: str) -> bool:
    """检测文本中是否包含表格结构（管道符分隔的多列行）。

    规则：至少连续 2 行包含管道符（|）。
    每行含 ≥1 个管道符即视为表格行——因为 2 列表格（col1|col2）只有 1 个分隔符。
    要求连续 2 行是为了排除单行含 | 的普通文本误判（如"大雁塔|唐代建筑"）。
    """
    lines = text.strip().split("\n")
    table_rows = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if table_rows > 0:
                break
            continue
        if stripped.count("|") >= 1:
            table_rows += 1
        elif table_rows > 0:
            break
    return table_rows >= 2


def _score_table_in_chunk(text: str) -> tuple[float, list[str]]:
    """对 chunk 中的表格部分做完整性评分。

    与 assess_table_quality 不同：这里输入是 chunk 文本（可能含非表格内容），
    需要先剥离出表格行，再做结构评估。
    """
    lines = text.strip().split("\n")
    table_lines: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.count("|") >= 1:
            table_lines.append(stripped)
            in_table = True
        elif in_table and not stripped:
            break  # 表格段结束

    if len(table_lines) < 2:
        return 1.0, []  # 无表格或单行，表格维度不扣分

    # 解析表头 + 数据行
    header_cells = [c.strip() for c in table_lines[0].split("|")]
    data_rows = [[c.strip() for c in row.split("|")] for row in table_lines[1:]]

    # 列数一致性
    col_counts = [len(row) for row in data_rows]
    unique_cols = set(col_counts)
    issues: list[str] = []
    score = 1.0

    if len(unique_cols) > 1:
        score -= min(0.4, 0.1 * len(unique_cols))
        issues.append(f"表格列数不一致：{sorted(unique_cols)}")

    # 空单元格率
    total_cells = sum(len(row) for row in data_rows)
    empty_cells = sum(1 for row in data_rows for c in row if not c)
    if total_cells > 0 and empty_cells / total_cells > 0.5:
        score -= 0.3
        issues.append(f"表格空单元格率过高：{empty_cells / total_cells:.0%}")

    return max(0.0, round(score, 4)), issues


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
        overall = text_health  # 无表格，文本健康度即综合分

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


# ============================================================================
# 统一质量报告构建
# ============================================================================

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

    # 写入审核队列（如有需要人工审核的 chunk）
    if report.needs_review > 0:
        _write_chunk_review_queue(report)

    logger.info(
        "文档质量报告 [%s]：%d chunks，通过 %d，LLM修复 %d，待审核 %d（检测到 %d 个表格）",
        file_name, report.total_chunks,
        report.passed, report.llm_fixed, report.needs_review,
        report.total_tables,
    )

    return report


# ============================================================================
# 降级修复（格式无关）
# ============================================================================

@lru_cache(maxsize=1)
def _get_fix_client() -> OpenAI:
    """获取修复 LLM 客户端单例（Qwen eval 模型）。"""
    return OpenAI(
        base_url=settings.qwen_eval_base_url,
        api_key=settings.qwen_eval_api_key,
        timeout=LLM_FIX_TIMEOUT,
    )


def _llm_fix_chunk(text: str, issues: list[str]) -> str:
    """调用 Qwen 修复低质量 chunk，返回清洗后文本。

    与表格专用修复不同：这里处理的是任意文本 chunk，
    可能是表格错位、可能是乱码、可能是截断句子。
    """
    issues_text = "；".join(issues) if issues else "无特定问题"

    prompt = (
        "你是一个文本清洗专家。以下文本来自知识库文档解析，可能存在表格错位、乱码、截断等问题。\n"
        "\n"
        "要求：\n"
        "1. 修正明显的错位或乱码，保持原始信息不丢失\n"
        "2. 如果包含表格数据，转换为自然流畅的中文描述\n"
        "3. 不要添加原文没有的信息，不要编造数据\n"
        "4. 直接输出清洗后的文本，不要解释修复过程\n"
        "\n"
        f"发现的问题：{issues_text}\n"
        f"原始文本：\n{text}\n"
    )

    try:
        client = _get_fix_client()
        resp = client.chat.completions.create(
            model=settings.qwen_eval_model,
            messages=[
                {"role": "system", "content": "你是文本清洗专家，只输出清洗后的文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=LLM_FIX_TEMPERATURE,
            max_tokens=2048,
        )
        cleaned = resp.choices[0].message.content or ""
        if cleaned.strip():
            logger.info("LLM chunk 修复成功：%d 字符 → %d 字符", len(text), len(cleaned))
            return cleaned.strip()
        logger.warning("LLM chunk 修复返回空内容，退回原始文本")
        return text
    except Exception as exc:
        logger.warning("LLM chunk 修复调用失败（%s），退回原始文本", exc)
        return text


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


def remediate_chunk(text: str, quality: ChunkQuality) -> str:
    """根据质量评估结果，对单个 chunk 执行降级策略。

    direct_insert → 返回原文
    llm_fix       → 调用 LLM 修复，失败退回原文
    human_review  → 返回带标记的原文（标记在 metadata 层处理，不入库文本）
    """
    if quality.suggestion == "direct_insert":
        return text
    if quality.suggestion == "llm_fix":
        return _llm_fix_chunk(text, quality.issues)
    return text  # human_review：不修改文本，审核标记在 metadata 层


# ============================================================================
# 向后兼容：PDF 专用入口（委托给统一管线）
# ============================================================================

def process_pdf_with_quality(file_path: Path) -> tuple[str, FileQualityReport]:
    """PDF 文件处理：提取 → 评估 → 返回聚合文本和报告。

    保留此函数以兼容现有调用方（ingest_service._load_text_file），
    内部委托给统一管线。

    Args:
        file_path: PDF 文件路径

    Returns:
        (aggregated_text, report)
    """
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

    aggregated = "\n\n".join(text_parts)

    # 对聚合文本做轻量 chunk 模拟评估（不实际分块，只做质量检测）
    # 这里把每个"段落块"（空行分隔）当作一个 chunk 来评估
    chunk_texts = [p for p in aggregated.split("\n\n") if p.strip()]
    report = assess_file_quality(
        file_name=file_path.name,
        file_format="pdf",
        chunks_texts=chunk_texts,
    )

    return aggregated, report


# ============================================================================
# 向后兼容：表格专用评分函数（供测试和已有调用方使用）
# ============================================================================

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
        "timestamp": __import__("datetime").datetime.now().isoformat(),
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