"""LLM 降级修复模块。

模块职责：
  - _get_fix_client: LLM 客户端单例（Qwen eval 模型）
  - _llm_fix_chunk: 调用 LLM 修复低质量 chunk
  - remediate_chunk: 根据质量评估结果执行降级策略

依赖：OpenAI SDK、config 常量、models 数据结构。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from openai import OpenAI

from app.config import settings
from app.services.quality.config import LLM_FIX_TEMPERATURE, LLM_FIX_TIMEOUT
from app.services.quality.models import ChunkQuality

logger = logging.getLogger(__name__)


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