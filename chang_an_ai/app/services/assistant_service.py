"""笔记 AI 辅助服务：标题 / 润色 / 情感分析（全部非流式，走 Java 转发）。

设计要点：
- 非流式短任务统一走 chat_sync；temperature 按任务性质选（标题要多样性→高，
  情感分析要确定性→低）——"生成任务高温、分析任务低温"是 LLM 工程常识。
- 所有解析都做容错：模型输出不完全可靠（偶尔带编号、代码围栏、前后缀），
  失败时回退到可用值，绝不让解析错误炸掉接口。
"""
from __future__ import annotations

import json
import re

from app.prompts.assistant import (
    POLISH_SYSTEM,
    SENTIMENT_SYSTEM,
    TITLE_SYSTEM,
    TITLE_USER_TEMPLATE,
)
from app.services.llm import chat_sync

TITLE_COUNT = 5
STYLES = ("文艺", "幽默", "朴实")


def generate_titles(content: str, shop_name: str = "", keywords: str = "") -> list[str]:
    """生成 5 个候选标题。"""
    messages = [
        {"role": "system", "content": TITLE_SYSTEM},
        {"role": "user", "content": TITLE_USER_TEMPLATE.format(
            shop_name=shop_name or "未关联商户",
            keywords=keywords or "无",
            content=content[:2000],  # 截断控制 token（标题不需要读完整篇）
        )},
    ]
    text = chat_sync(messages, temperature=0.9, max_tokens=200)
    titles = [line.strip(" 　-·•·*#") for line in text.splitlines()]
    titles = [t for t in titles if t and len(t) <= 40][:TITLE_COUNT]
    return titles or [content[:30] or "探店笔记"]


def polish(content: str, style: str = "文艺") -> str:
    """按风格润色正文。style 白名单校验，非法值回退「文艺」。"""
    if style not in STYLES:
        style = "文艺"
    messages = [
        {"role": "system", "content": POLISH_SYSTEM.format(style=style)},
        {"role": "user", "content": content[:3000]},
    ]
    try:
        text = chat_sync(messages, temperature=0.7).strip()
        return text or content  # 空输出回退原文
    except Exception:
        return content  # LLM 挂了回退原文——辅助功能不能挡住用户发笔记


def analyze_sentiment(text: str) -> dict:
    """情感分析 → {sentiment, score, keywords, summary}。

    解析容错：模型可能输出 ```json 围栏或前后缀文字，
    用正则先捞出第一个 {...} 再 json.loads。
    """
    messages = [
        {"role": "system", "content": SENTIMENT_SYSTEM},
        {"role": "user", "content": text[:2000]},
    ]
    resp = chat_sync(messages, temperature=0.1, max_tokens=200)
    match = re.search(r"\{.*\}", resp, re.S)
    if not match:
        return {"sentiment": "中性", "score": 50, "keywords": [], "summary": "分析失败，请重试"}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return {"sentiment": "中性", "score": 50, "keywords": [], "summary": "分析失败，请重试"}
    return {
        "sentiment": str(data.get("sentiment", "中性")),
        "score": int(data.get("score", 50)),
        "keywords": [str(k) for k in data.get("keywords", [])],
        "summary": str(data.get("summary", "")),
    }
