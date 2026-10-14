"""文本健康度 & 表格完整性评分函数。

模块职责：
  - _score_text_health: 对 chunk 文本做多维健康检查（长度/空白/乱码/有效内容）
  - _detect_tables_in_text: 检测 chunk 中是否包含表格结构
  - _is_markdown_separator_row: 识别 Markdown 表格分隔行
  - _split_table_cells: 按 | 拆分表格行，清理前导/尾随空单元格
  - _score_table_in_chunk: 对 chunk 中的表格部分做完整性评分

纯函数，无副作用，不依赖 OpenAI/文件系统。
"""
from __future__ import annotations

import re

logger = __import__("logging").getLogger(__name__)


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


# ============================================================================
# 表格检测与解析
# ============================================================================

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


def _is_markdown_separator_row(row: str) -> bool:
    """判断是否为 Markdown 表格分隔行，如 |---|---| 或 |:---:|:---|。

    特征：每个单元格仅由 -、:、空格组成。
    """
    cells = [c.strip() for c in row.split("|") if c.strip()]
    if not cells:
        return False
    return all(bool(re.fullmatch(r"[-:\s]+", c)) for c in cells)


def _split_table_cells(row: str) -> list[str]:
    """按 | 拆分表格行，去除前导/尾随空单元格（由 |...| 包裹格式产生）。

    只移除由包裹语法（行首/行尾的 |）引入的空单元格，保留中间的空白单元格。
    例：
        '| H1 | H2 |' → ['H1', 'H2']
        'H1 | H2'     → ['H1', 'H2']
        '| | | |'     → ['', '', '']   （保留中间空单元格，用于空值率检测）
        '| data | | extra |' → ['data', '', 'extra']
    """
    cells = [c.strip() for c in row.split("|")]
    stripped_row = row.strip()
    if cells and stripped_row.startswith("|") and not cells[0]:
        cells.pop(0)
    if cells and stripped_row.endswith("|") and not cells[-1]:
        cells.pop()
    return cells


def _score_table_in_chunk(text: str) -> tuple[float, list[str]]:
    """对 chunk 中的表格部分做完整性评分。

    与 assess_table_quality 不同：这里输入是 chunk 文本（可能含非表格内容），
    需要先剥离出表格行，再做结构评估。

    兼容 Markdown 表格：自动跳过 |---|---| 分隔行，
    消除 |...| 包裹格式带来的前导/尾随空单元格。
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
            break

    if len(table_lines) < 2:
        return 1.0, []

    header_cells = _split_table_cells(table_lines[0])
    data_rows: list[list[str]] = []
    for row in table_lines[1:]:
        if _is_markdown_separator_row(row):
            continue
        data_rows.append(_split_table_cells(row))

    if not data_rows:
        return 1.0, []

    col_counts = [len(row) for row in data_rows]
    unique_cols = set(col_counts)
    issues: list[str] = []
    score = 1.0

    if len(unique_cols) > 1:
        score -= min(0.4, 0.1 * len(unique_cols))
        issues.append(f"表格列数不一致：{sorted(unique_cols)}")

    total_cells = sum(len(row) for row in data_rows)
    empty_cells = sum(1 for row in data_rows for c in row if not c)
    if total_cells > 0 and empty_cells / total_cells > 0.5:
        score -= 0.3
        issues.append(f"表格空单元格率过高：{empty_cells / total_cells:.0%}")

    return max(0.0, round(score, 4)), issues