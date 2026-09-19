"""多轮对话 query 改写：把"它几点关门"还原成"大雁塔几点关门"。

- 启发式短路：无指代词的问句直接原样返回，省一次 LLM 调用和几百毫秒延迟；
  "能用规则就不用模型"——成本与延迟控制的基本功。
- LLM 改写失败时回退原问句：改写是增强不是命脉，绝不能让它阻塞主流程。
"""
from __future__ import annotations

from app.prompts.rewrite import REWRITE_SYSTEM, REWRITE_USER_TEMPLATE
from app.services.llm import chat_sync

# 出现这些指代/回指词才需要改写（列表可按实际语料扩充）
_PRONOUNS = (
    "它", "他", "她", "这个", "那个", "这家", "那家", "这里", "那里",
    "刚才", "之前", "上面的", "还有", "呢", "怎么样",
)


def needs_rewrite(question: str) -> bool:
    return any(p in question for p in _PRONOUNS)


def rewrite(question: str, history: list[dict]) -> str:
    """history: [{role, content}]（含当前问题前的近 3 轮）。

    只把近 6 条消息（3 轮）喂给 LLM：够指代消解用，又控制 token 成本。
    """
    if not needs_rewrite(question):
        return question
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": REWRITE_USER_TEMPLATE.format(history=history_text, question=question)},
    ]
    try:
        rewritten = chat_sync(messages, temperature=0.1, max_tokens=100).strip()
        return rewritten or question  # 空输出回退原句
    except Exception:
        return question  # 网络异常回退原句，改写失败不阻塞问答
