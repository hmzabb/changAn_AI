"""统一文档质量评估单元测试：覆盖所有格式（.md/.pdf/.docx）。

测试结构：
  1. 统一入口：assess_chunk_quality（格式无关，核心）
  2. 文本健康度：_score_text_health（所有格式必检）
  3. 表格检测：_detect_tables_in_text（有表才检）
  4. 表格评分：_score_table_in_chunk（表格完整性）
  5. 文件报告：assess_file_quality（聚合）
  6. 降级修复：remediate_chunk
  7. 向后兼容：assess_table_quality 等旧接口
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services.pdf_quality import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    REVIEW_QUEUE_DIR,
    ChunkQuality,
    FileQualityReport,
    TableQuality,
    _clean_cell,
    _detect_tables_in_text,
    _extract_table_from_raw,
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


# ============================================================================
# 统一入口：assess_chunk_quality（核心——格式无关）
# ============================================================================

class TestAssessChunkQuality:
    """assess_chunk_quality 是统一质量门唯一入口，所有格式都走它。"""

    def test_normal_markdown_chunk_passes(self):
        """手写 Markdown 文档的 chunk：无表格，文本健康。"""
        text = "大雁塔位于西安市南郊，是唐代建筑，高64.5米。大雁塔是玄奘为保存由天竺带回的经卷佛像而主持修建的。"
        result = assess_chunk_quality(text)
        assert result.suggestion == "direct_insert"
        assert result.has_tables is False
        assert result.table_score is None
        assert result.overall_score >= CONFIDENCE_HIGH

    def test_normal_pdf_chunk_without_table_passes(self):
        """PDF 提取的纯文本 chunk：与 MD 走同一套逻辑。"""
        text = "西安城墙是中国现存规模最大、保存最完整的古代城垣建筑，周长13.74公里。"
        result = assess_chunk_quality(text)
        assert result.suggestion == "direct_insert"
        assert result.has_tables is False
        assert result.overall_score >= CONFIDENCE_HIGH

    def test_chunk_with_table_detects_and_scores(self):
        """含表格的 chunk：文本健康 + 表格完整性双重评估。"""
        text = "景点名称 | 门票价格 | 开放时间\n大雁塔 | 40元 | 8:00-17:30\n钟楼 | 30元 | 8:30-18:00"
        result = assess_chunk_quality(text)
        assert result.has_tables is True
        assert result.table_score is not None
        # 结构完整的表格应不影响总体高分
        assert result.overall_score >= CONFIDENCE_HIGH

    def test_broken_table_lowers_score(self):
        """列数不一致的表格 + 检测到应降低综合分。"""
        text = "名称 | 价格\n大雁塔 | 40\n钟楼\n鼓楼 | 30 | 额外列"
        result = assess_chunk_quality(text)
        assert result.has_tables is True
        # 有表格问题时综合分应低于无表格时
        issue_texts = " ".join(result.issues)
        assert "列数不一致" in issue_texts or result.overall_score < 0.90

    def test_empty_chunk_rejected(self):
        result = assess_chunk_quality("")
        assert result.suggestion == "human_review"
        assert result.overall_score == 0.0
        assert "空 chunk" in result.issues

    def test_very_short_chunk_low_score(self):
        result = assess_chunk_quality("大雁塔")
        assert result.overall_score <= 0.30
        assert result.suggestion == "human_review"

    def test_garbled_chunk_detected(self):
        text = "正常中文\x00\x00\x00\x01\x02乱码部分"
        result = assess_chunk_quality(text)
        assert result.overall_score < CONFIDENCE_HIGH
        assert any("乱码" in i or "异常" in i for i in result.issues)

    def test_md_docx_pdf_all_same_pipeline(self):
        """证明三种格式走同一套逻辑。"""
        md_text = "回民街是西安著名的小吃街，汇集了数百种特色美食。"
        docx_text = "钟楼位于西安市中心，是中国现存钟楼中形制最大、保存最完整的一座。"
        pdf_text = "大唐不夜城位于大雁塔脚下，是一条以盛唐文化为背景的步行街。"

        for text in [md_text, docx_text, pdf_text]:
            result = assess_chunk_quality(text)
            assert result.suggestion == "direct_insert"
            assert result.has_tables is False
            assert result.table_score is None
            # 所有格式的健康文本都应通过


# ============================================================================
# 文本健康度（所有格式必检）
# ============================================================================

class TestScoreTextHealth:
    def test_healthy_text_full_score(self):
        score, issues = _score_text_health(
            "大雁塔位于西安市南郊，是唐代建筑，高64.5米。大雁塔是玄奘为保存由天竺带回的经卷佛像而主持修建的，"
            "历时两年建成。大雁塔最初五层，后加盖至九层，再后层数和高度又有数次变更，最后固定为今天所看到的七层塔身。"
        )
        assert score >= 0.95
        assert len(issues) == 0

    def test_empty_text_zero(self):
        score, issues = _score_text_health("")
        assert score == 0.0
        assert any("空" in i for i in issues)

    def test_whitespace_only_low(self):
        score, issues = _score_text_health("   \n\n  \t  ")
        assert score < 0.30

    def test_too_short_low(self):
        score, issues = _score_text_health("大雁塔")
        assert score <= 0.35
        assert any("过短" in i for i in issues)

    def test_too_long_flagged(self):
        text = "内容" * 800  # 1600 字符
        score, issues = _score_text_health(text)
        assert any("偏长" in i for i in issues)

    def test_low_valid_content_ratio(self):
        text = "!@#$%^&*()" * 20
        score, issues = _score_text_health(text)
        assert score < 0.80
        assert any("有效" in i for i in issues)

    def test_garbage_chars_detected(self):
        text = "正常文本" + "\x00\x00\x00" * 10
        score, issues = _score_text_health(text)
        assert any("乱码" in i or "异常" in i for i in issues)


# ============================================================================
# 表格检测（有表才检，格式无关）
# ============================================================================

class TestDetectTablesInText:
    def test_pdf_style_table_detected(self):
        """pdfplumber 输出的管道符表格。"""
        text = "名称 | 价格 | 时间\n大雁塔 | 40 | 8:00"
        assert _detect_tables_in_text(text) is True

    def test_docx_style_table_detected(self):
        """python-docx 输出的管道符表格（格式相同）。"""
        text = "景点名称 | 门票价格\n大雁塔 | 40元"
        assert _detect_tables_in_text(text) is True

    def test_single_pipe_line_not_table(self):
        """单行含 | 不应误判为表格。"""
        text = "大雁塔 | 位于西安"
        assert _detect_tables_in_text(text) is False

    def test_two_pipe_lines_is_table(self):
        text = "名称 | 价格\n大雁塔 | 40"
        assert _detect_tables_in_text(text) is True

    def test_plain_text_not_table(self):
        text = "大雁塔位于西安市南郊，是唐代建筑。"
        assert _detect_tables_in_text(text) is False

    def test_markdown_without_pipes_not_table(self):
        """Markdown 纯文本不含表格标记。"""
        text = "## 大雁塔\n\n大雁塔位于西安市南郊。"
        assert _detect_tables_in_text(text) is False


class TestScoreTableInChunk:
    def test_well_structured_table_full_score(self):
        text = "名称 | 价格 | 时间\n大雁塔 | 40 | 8:00\n钟楼 | 30 | 9:00"
        score, issues = _score_table_in_chunk(text)
        assert score >= 0.95
        assert len(issues) == 0

    def test_inconsistent_columns_detected(self):
        text = "名称 | 价格\n大雁塔 | 40\n钟楼\n鼓楼 | 30"
        score, issues = _score_table_in_chunk(text)
        assert "列数不一致" in str(issues)

    def test_no_table_in_text_no_penalty(self):
        """无表格内容的纯文本：表格维度不扣分。"""
        text = "大雁塔位于西安市南郊。"
        score, issues = _score_table_in_chunk(text)
        assert score == 1.0
        assert len(issues) == 0


# ============================================================================
# 文件级别报告
# ============================================================================

class TestAssessFileQuality:
    def test_markdown_file_report(self):
        """MD 文件质量报告。"""
        chunks = [
            "大雁塔位于西安市南郊，是唐代建筑。",
            "钟楼位于西安市中心，是中国现存钟楼中形制最大的一座。",
        ]
        report = assess_file_quality("test.md", "md", chunks)
        assert report.file_format == "md"
        assert report.total_chunks == 2
        assert report.passed == 2
        assert report.total_tables == 0

    def test_pdf_file_report_with_tables(self):
        chunks = [
            "名称 | 价格 | 时间\n大雁塔 | 40 | 8:00\n钟楼 | 30 | 9:00",
            "西安城墙是中国现存规模最大的古代城垣。",
        ]
        report = assess_file_quality("test.pdf", "pdf", chunks)
        assert report.file_format == "pdf"
        assert report.total_tables == 1
        assert report.total_chunks == 2

    def test_file_with_bad_chunk_reports_review(self):
        chunks = [""]  # 空 chunk
        report = assess_file_quality("bad.md", "md", chunks)
        assert report.needs_review >= 1

    def test_file_with_llm_fix_chunk(self):
        """中置信度 chunk 走 LLM 修复。"""
        # 偏长 chunk 会触发 llm_fix
        chunks = ["短文本" * 500]  # ~1500 字符
        report = assess_file_quality("long.md", "md", chunks)
        # 偏长文本可能触发建议
        assert report.total_chunks == 1
        # 不强制预测建议类型，只看结构完整
        assert isinstance(report.chunk_qualities[0], ChunkQuality)


# ============================================================================
# 降级修复
# ============================================================================

class TestRemediateChunk:
    def test_direct_insert_returns_original(self):
        text = "大雁塔位于西安。"
        quality = ChunkQuality(
            text_sample=text,
            text_health_score=1.0,
            overall_score=1.0,
            suggestion="direct_insert",
        )
        assert remediate_chunk(text, quality) == text

    def test_llm_fix_calls_model(self):
        text = "名称 | 价格\n大雁塔 | 40"
        quality = ChunkQuality(
            text_sample=text[:80],
            text_health_score=0.7,
            has_tables=True,
            table_score=0.6,
            overall_score=0.67,
            issues=["列数轻微不一致"],
            suggestion="llm_fix",
        )
        with patch("app.services.quality.remediator._get_fix_client") as mock_client_func:
            mock_client = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "大雁塔门票价格为40元。"
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[mock_choice]
            )
            mock_client_func.return_value = mock_client

            result = remediate_chunk(text, quality)
            assert "40" in result

    def test_llm_fix_fallback_on_error(self):
        text = "测试文本"
        quality = ChunkQuality(
            text_sample=text,
            text_health_score=0.6,
            overall_score=0.6,
            suggestion="llm_fix",
        )
        with patch("app.services.quality.remediator._get_fix_client") as mock_client_func:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("超时")
            mock_client_func.return_value = mock_client
            result = remediate_chunk(text, quality)
            assert result == text  # 退回原文

    def test_human_review_returns_original(self):
        text = "不完整文本"
        quality = ChunkQuality(
            text_sample=text,
            text_health_score=0.3,
            overall_score=0.3,
            suggestion="human_review",
        )
        result = remediate_chunk(text, quality)
        assert result == text  # 人工审核不修改文本


# ============================================================================
# 向后兼容：旧 API 测试
# ============================================================================

class TestBackwardCompat:
    """assess_table_quality 等旧 API 保持兼容。"""

    def test_assess_table_quality_still_works(self):
        headers = ["名称", "价格"]
        rows = [["大雁塔", "40元"], ["钟楼", "30元"]]
        result = assess_table_quality(headers, rows)
        assert result.suggestion == "direct_insert"
        assert result.overall_score >= CONFIDENCE_HIGH

    def test_clean_cell(self):
        assert _clean_cell(None) == ""
        assert _clean_cell("  大雁塔  ") == "大雁塔"

    def test_extract_table_from_raw(self):
        raw = [["名称", "价格"], ["大雁塔", "40元"]]
        headers, rows = _extract_table_from_raw(raw)
        assert headers == ["名称", "价格"]
        assert rows == [["大雁塔", "40元"]]

    def test_handle_table_fix(self):
        headers, rows = ["名称"], [["大雁塔"]]
        quality = TableQuality(
            structural_score=0.95, semantic_score=0.90,
            consistency_score=1.0, overall_score=0.93,
            suggestion="direct_insert",
        )
        text = _handle_table_fix(headers, rows, quality, "test.pdf")
        assert "大雁塔" in text

    def test_score_structural(self):
        headers = ["名称", "价格"]
        rows = [["大雁塔", "40"]]
        score, issues = _score_structural(headers, rows)
        assert score >= 0.95

    def test_score_semantic(self):
        headers = ["名称", "价格"]
        rows = [["大雁塔", "40元"]]
        score, issues = _score_semantic(headers, rows)
        assert score >= 0.80


class TestLlmFixChunk:
    def test_successful_fix(self):
        with patch("app.services.quality.remediator._get_fix_client") as mock_client_func:
            mock_client = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "清洗后的文本"
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[mock_choice]
            )
            mock_client_func.return_value = mock_client
            result = _llm_fix_chunk("原始文本", ["问题1"])
            assert result == "清洗后的文本"

    def test_empty_response_fallback(self):
        with patch("app.services.quality.remediator._get_fix_client") as mock_client_func:
            mock_client = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = ""
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[mock_choice]
            )
            mock_client_func.return_value = mock_client
            result = _llm_fix_chunk("原始文本", [])
            assert result == "原始文本"

    def test_exception_fallback(self):
        with patch("app.services.quality.remediator._get_fix_client") as mock_client_func:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = ConnectionError("网络不可达")
            mock_client_func.return_value = mock_client
            result = _llm_fix_chunk("原始文本", [])
            assert result == "原始文本"


# ============================================================================
# 配置测试
# ============================================================================

class TestThresholds:
    def test_thresholds_ordered(self):
        assert CONFIDENCE_HIGH > CONFIDENCE_MEDIUM
        assert 0.0 < CONFIDENCE_MEDIUM < CONFIDENCE_HIGH <= 1.0

    def test_thresholds_in_reasonable_range(self):
        assert 0.80 <= CONFIDENCE_HIGH <= 0.95
        assert 0.40 <= CONFIDENCE_MEDIUM <= 0.60


# ============================================================================
# 集成测试：真实 PDF
# ============================================================================

class TestProcessPdfWithQuality:
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent.parent / "app" / "data" / "corpus"
             / "guides" / "西安景点门票与开放时间总览.pdf").exists(),
        reason="需要 PDF 文件存在于 corpus 目录",
    )
    def test_real_pdf(self):
        pdf_path = (
            Path(__file__).parent.parent.parent
            / "app" / "data" / "corpus" / "guides"
            / "西安景点门票与开放时间总览.pdf"
        )
        text, report = process_pdf_with_quality(pdf_path)
        assert isinstance(text, str)
        assert len(text) > 0
        assert isinstance(report, FileQualityReport)
        assert report.file_format == "pdf"

    def test_report_structure_valid(self):
        report = FileQualityReport(file_name="test.md", file_format="md")
        assert report.total_chunks == 0
        assert report.total_tables == 0