"""建库流水线：拉数 → 模板化/分块 → embedding → 入 Milvus。

被两处复用：scripts/ingest.py（离线命令行）与 routers/admin.py（HTTP 触发），
流水线逻辑只写一份，避免"脚本里一套、接口里一套"各自漂移。

幂等设计：每个 source 先 delete_by_source 清空再重灌——数据删改后重跑不会残留旧 chunk。
部分失败设计：java 源失败不影响 corpus 源入库，报告里带 errors 字段，谁挂了一目了然。
"""
from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR, settings
from app.repositories.java_client import JavaClient, JavaClientError
from app.repositories.vector_store import get_store
from app.services.chunking import Chunk, blog_to_text, chunk_markdown, shop_to_text, voucher_to_text
from app.services.embedding import get_embedding_client

CORPUS_DIR = BASE_DIR / "app" / "data" / "corpus"
EMBED_BATCH_SIZE = 16  # 每批 16 条调一次 embedding API：省调用次数与费用（接口支持批量输入）


def load_corpus_chunks(corpus_dir: Path | None = None) -> list[Chunk]:
    """读 corpus/**/*.md → Chunk（source=corpus）。文件名（去扩展名）即文档标题。"""
    chunks: list[Chunk] = []
    for md_file in sorted((corpus_dir or CORPUS_DIR).rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if not text.strip():
            continue
        chunks.extend(chunk_markdown(text, doc_title=md_file.stem, file_name=md_file.name))
    return chunks


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

    返回 {"sources": {源: chunk数}, "errors": [...], "embedding": 模型名}。
    """
    sources = sources or ["corpus", "java"]
    embedder = get_embedding_client()
    store = get_store()
    report: dict = {"sources": {}, "errors": [], "embedding": settings.embedding_model}

    for source in sources:
        # ---- 1. 加载本源的 chunks ----
        try:
            if source == "corpus":
                chunks = load_corpus_chunks()
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
