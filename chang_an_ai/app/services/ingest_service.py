"""建库流水线：拉数 → 模板化/分块 → embedding → 入 Milvus。

被两处复用：scripts/ingest.py（离线命令行）与 routers/admin.py（HTTP 触发），
流水线逻辑只写一份，避免"脚本里一套、接口里一套"各自漂移。

幂等设计：每个 source 先 delete_by_source 清空再重灌——数据删改后重跑不会残留旧 chunk。
部分失败设计：java 源失败不影响 corpus 源入库，报告里带 errors 字段，谁挂了一目了然。

PDF 表格质量保障（v2）：
  PDF 文件走三段式质量管线：结构化提取 → 置信度评估 → 降级修复。
  高质量表格直接入库，中置信度走 LLM 修复，低置信度写入审核队列但不阻塞入库。
  质量报告注入 run_ingest 返回值的 table_quality 字段，运维可据此排查。
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import BASE_DIR, settings
from app.repositories.java_client import JavaClient, JavaClientError
from app.repositories.vector_store import get_store
from app.services.chunking import Chunk, blog_to_text, chunk_markdown, chunk_plain_text, shop_to_text, voucher_to_text
from app.services.embedding import get_embedding_client
from app.services.pdf_quality import FileQualityReport, assess_file_quality, process_pdf_with_quality

logger = logging.getLogger(__name__)

CORPUS_DIR = BASE_DIR / "app" / "data" / "corpus"
EMBED_BATCH_SIZE = 16

# 支持的文件扩展名 → 解析函数映射
_SUPPORTED_EXTENSIONS = {".md", ".pdf", ".docx"}


def _load_text_file(file_path: Path) -> str:
    """读取单个文件文本。PDF 文件走质量管线（仅用返回的聚合文本）。"""
    ext = file_path.suffix.lower()
    if ext == ".md":
        return file_path.read_text(encoding="utf-8")
    elif ext == ".pdf":
        text, _report = process_pdf_with_quality(file_path)
        return text
    elif ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError("请安装 python-docx：pip install python-docx>=1.1.0")
        doc = Document(str(file_path))
        parts = []
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
    else:
        raise ValueError(f"不支持的文件格式：{ext}")

def load_corpus_chunks(corpus_dir: Path | None = None) -> tuple[list[Chunk], list[FileQualityReport]]:
    """读 corpus/ 下支持的文件（.md/.pdf/.docx）→ Chunk（source=corpus）。

    文件名（去扩展名）即文档标题。根据后缀自动选择解析器和分块策略：
    .md → chunk_markdown（按 ## 标题切）；.pdf/.docx → chunk_plain_text（按段落切）。

    所有格式文件都会走统一质量门（assess_file_quality），
    在 chunk 生成之后、入库之前对每个 chunk 做文本健康度 + 表格完整性评估。

    Returns:
        (chunks, quality_reports)：chunks 为待入库列表，quality_reports 为所有文件的质量报告。
    """
    chunks: list[Chunk] = []
    quality_reports: list[FileQualityReport] = []
    target_dir = corpus_dir or CORPUS_DIR
    for ext in _SUPPORTED_EXTENSIONS:
        for file_path in sorted(target_dir.rglob(f"*{ext}")):
            # ---- 1. 提取文本 ----
            try:
                if ext == ".pdf":
                    text, _qr_hint = process_pdf_with_quality(file_path)
                    # PDF 提取阶段已有一次评估，chunk 后的评估更精确（覆盖表格截断场景）
                else:
                    text = _load_text_file(file_path)
            except Exception as e:
                logger.warning("读取 %s 失败: %s", file_path.name, e)
                continue
            if not text.strip():
                continue

            # ---- 2. 分块 ----
            if ext == ".md":
                file_chunks = chunk_markdown(text, doc_title=file_path.stem, file_name=file_path.name)
            else:
                file_chunks = chunk_plain_text(text, doc_title=file_path.stem, file_name=file_path.name)

            if not file_chunks:
                continue

            # ---- 3. 统一质量门（所有格式都走）----
            fmt = ext.lstrip(".")
            chunk_texts = [c.text for c in file_chunks]
            qr = assess_file_quality(
                file_name=file_path.name,
                file_format=fmt,
                chunks_texts=chunk_texts,
            )
            quality_reports.append(qr)

            # ---- 4. LLM 修复（需要修复的 chunk 走降级策略）----
            for i, cq in enumerate(qr.chunk_qualities):
                if cq.suggestion == "llm_fix":
                    from app.services.pdf_quality import remediate_chunk
                    file_chunks[i].text = remediate_chunk(file_chunks[i].text, cq)

            chunks.extend(file_chunks)

    return chunks, quality_reports


def load_java_chunks() -> list[Chunk]:
    """拉 Java 数据 → 模板化 → Chunk（source=java）。

    失败时抛 JavaClientError，调用方负责报错提示。
    数据顺序：分类表 → 店铺（按分类翻页）→ 笔记（翻页）→ 券（按店查）。
    """
    chunks: list[Chunk] = []
    with JavaClient() as jc:
        type_names = {t["id"]: t["name"] for t in jc.list_shop_types()}
        shops = jc.fetch_all_shops()
        for shop in shops:
            chunks.append(
                Chunk(
                    text=shop_to_text(shop, type_name=type_names.get(shop.get("typeId"), "")),
                    metadata={
                        "source": "java",
                        "type": "shop",
                        "id": shop.get("id"),
                        "name": shop.get("name"),
                        "area": shop.get("area"),  # 阶段 2 重排"商圈匹配"要用
                    },
                )
            )
        for blog in jc.fetch_all_blogs():
            chunks.append(
                Chunk(
                    text=blog_to_text(blog),
                    metadata={
                        "source": "java",
                        "type": "blog",
                        "id": blog.get("id"),
                        "shop_id": blog.get("shopId"),  # 前端"来源卡片"跳店铺详情要用
                        "title": blog.get("title"),
                    },
                )
            )
        for shop in shops:
            for voucher in jc.list_vouchers(shop["id"]):
                chunks.append(
                    Chunk(
                        text=voucher_to_text(voucher, shop_name=shop.get("name", "")),
                        metadata={
                            "source": "java",
                            "type": "voucher",
                            "id": voucher.get("id"),
                            "shop_id": shop.get("id"),
                        },
                    )
                )
    return chunks


def run_ingest(sources: list[str] | None = None, progress=None) -> dict:
    """重建知识库。sources: ["corpus","java"]（默认全部）；progress(msg) 可选进度回调(实时报告进度)。
    返回 {"sources": {源: chunk数}, "errors": [...], "embedding": 模型名, "table_quality": [...]}。
    table_quality 字段记录 PDF 表格置信度评估结果，为空表示无 PDF 或无表格。
    """
    sources = sources or ["corpus", "java"]
    embedder = get_embedding_client()
    store = get_store()
    report: dict = {"sources": {}, "errors": [], "embedding": settings.embedding_model, "table_quality": []}

    for source in sources:
        # ---- 1. 加载本源的 chunks ----
        try:
            if source == "corpus":
                chunks, quality_reports = load_corpus_chunks()
                # 聚合所有 PDF 的质量报告
                for qr in quality_reports:
                    report["table_quality"].append({
                        "file": qr.file_name,
                        "format": qr.file_format,
                        "total_chunks": qr.total_chunks,
                        "total_tables": qr.total_tables,
                        "passed": qr.passed,
                        "llm_fixed": qr.llm_fixed,
                        "needs_review": qr.needs_review,
                        "errors": qr.errors,
                    })
                    if qr.needs_review > 0:
                        report["errors"].append(
                            f"审核队列：{qr.file_name}（{qr.file_format}）有 {qr.needs_review} 个 chunk 需人工审核，"
                            f"队列文件见 app/data/review_queue/"
                        )
            elif source == "java":
                chunks = load_java_chunks()
            else:
                report["errors"].append(f"未知 source: {source}")
                continue
        except JavaClientError as e:
            report["errors"].append(f"java 源失败（请确认 Java 8081 已启动、MySQL/Redis 正常）: {e}")
            continue
        except Exception as e:  # 单个源失败不拖垮整个建库
            report["errors"].append(f"{source} 源加载失败: {e}")
            continue
        if not chunks:
            report["sources"][source] = 0
            continue

        # ---- 2. 先清空再分批 embed + upsert（幂等重建）----
        store.delete_by_source(source)
        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i : i + EMBED_BATCH_SIZE]
            # 调用大模型将切分的文本向量化
            embeddings = embedder.embed_texts([c.text for c in batch])
            store.upsert_chunks(batch, embeddings)
            if progress:
                progress(f"[{source}] {min(i + EMBED_BATCH_SIZE, len(chunks))}/{len(chunks)} chunks 已入库")
        report["sources"][source] = len(chunks)

    report["stats"] = store.source_stats()
    return report