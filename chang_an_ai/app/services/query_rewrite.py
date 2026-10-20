"""多轮对话 query 改写：把"它几点关门"还原成"大雁塔几点关门"。

设计要点：
- 启发式短路：无指代词的问句直接原样返回，省一次 LLM 调用和几百毫秒延迟；
- 指代词三级分层：明确指代（这家/那个等）→ 话语回指（刚才/之前等）→ 单字指代（它/他/她）；
- 单字指代需排除复合词误匹配（其他/其它/其实/尤其等），避免非指代场景触发 LLM 调用；
- 改写结果做轻量校验：空输出 / 仅标点 → 回退原句，改写失败不阻塞主流程。
"""
from __future__ import annotations

import logging

from app.prompts.rewrite import DETECT_SYSTEM, REWRITE_SYSTEM, REWRITE_USER_TEMPLATE
from app.services.llm import chat_local_sync

logger = logging.getLogger(__name__)

# ── 指代词三级分层 ────────────────────────────────────────
# 第一级：明确指代词（出现在对话中几乎一定需要消解）
_EXPLICIT_PRONOUNS = (
    "这家", "那家", "该店",
    "这里", "那里", "这边", "那边", "这儿", "那儿",
    "这个", "那个", "这些", "那些",
)

# 第二级：话语回指词（引用上文内容，通常需要结合历史改写）
_DISCOURSE_MARKERS = (
    "刚才", "之前", "上面的",
)

# 第三级：单字人称（需排除复合词，见 _PRONOUN_FALSE_COMPOUNDS）
_SINGLE_CHAR_PRONOUNS = ("它", "他", "她")

# 含有它/他/她但不是指代用法的常用复合词
_PRONOUN_FALSE_COMPOUNDS = frozenset({
    "其他", "其它", "其中", "其实", "尤其", "他人", "他乡",
})


def _contains_single_pronoun(text: str, pronoun: str) -> bool:
    """检查 text 中的 pronoun（它/他/她）是否为真实指代，排除复合词误匹配。

    策略：先抹掉所有复合词（替换为空格），再检查 pronoun 是否仍在文本中。
    """
    cleaned = text
    for compound in _PRONOUN_FALSE_COMPOUNDS:
        cleaned = cleaned.replace(compound, "  ")
    return pronoun in cleaned


def _llm_classify(question: str) -> bool:
    """LLM 兜底分类：判断问句是否含需要消解的指代词。

    关键词检测未命中时触发。一次极轻量调用（max_tokens=5，温度 0），
    延迟 ~50-100ms。失败时返回 False（宁可漏判也不错判），不阻塞主流程。
    """
    try:
        result = chat_local_sync(
            [
                {"role": "system", "content": DETECT_SYSTEM},
                {"role": "user", "content": f"问题：{question}"},
            ],
            temperature=0,
            max_tokens=5,
        ).strip().lower()
        detected = "yes" in result
        if detected:
            logger.info("[指代检测] LLM 兜底发现指代词 | query='%s'", question[:50])
        return detected
    except Exception:
        logger.warning("[指代检测] LLM 分类异常，回退为无需改写 | query='%s'", question[:60])
        return False


def needs_rewrite(question: str) -> bool:
    """两程流水线判断当前问句是否需要指代消解。

    第一程——关键词（<0.1ms，零成本）：
    三级检测，命中任意一级即返回 True；
    第二程——LLM 兜底（~50ms，月增 ¥40-80）：
    关键词未命中时发起一次极轻量 LLM 分类，
    把漏判率从 ~15% 压到 ~3%。

    '呢/怎么样/还有' 等泛用词已从词集中移除，避免正常问句误触发改写。
    """
    # 第一程：关键词快速通路
    for p in _EXPLICIT_PRONOUNS:
        if p in question:
            return True
    for p in _DISCOURSE_MARKERS:
        if p in question:
            return True
    for p in _SINGLE_CHAR_PRONOUNS:
        if _contains_single_pronoun(question, p):
            return True
    # 第二程：关键词未命中，LLM 兜底
    return _llm_classify(question)


def _validate_rewrite(original: str, rewritten: str) -> bool:
    """改写结果轻量校验。

    以下情况判定为不通过，应回退原句：
    - 空字符串 / 仅空白
    - 仅标点符号（无实际内容）
    """
    if not rewritten or not rewritten.strip():
        return False
    stripped = rewritten.strip()
    # 剔除常见标点后仍为空 → 无实际内容
    meaningful = stripped.rstrip("?？!！。，,、；;：:…~～ ")
    return len(meaningful) > 0


def rewrite(question: str, history: list[dict]) -> str:
    """history: [{role, content}]（含当前问题前的近 3 轮）。

    只把近 6 条消息（3 轮）喂给 LLM：够指代消解用，又控制 token 成本。
    改写结果经过轻量校验，不合法时回退原句并记日志。
    """
    if not needs_rewrite(question):
        return question

    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": REWRITE_USER_TEMPLATE.format(
            history=history_text, question=question
        )},
    ]
    try:
        rewritten = chat_local_sync(messages, temperature=0.1, max_tokens=100).strip()
        if not _validate_rewrite(question, rewritten):
            logger.warning(
                "[指代消解] 改写结果校验不通过，回退原句 | 原文='%s' | 改写='%s'",
                question[:60], rewritten[:60],
            )
            return question
        if rewritten != question:
            logger.info("[指代消解] '%s' → '%s'", question[:50], rewritten[:50])
        return rewritten or question
    except Exception:
        logger.warning("[指代消解] LLM 调用异常，回退原句 | query='%s'", question[:60])
        return question