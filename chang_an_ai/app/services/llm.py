"""DeepSeek 大模型封装（openai SDK，OpenAI 兼容协议）。

项目里所有 LLM 调用都经过这里：将来加重试/限流/切换模型只改一处。
流式与非流式分开两个函数——短任务（query 改写）用 sync，聊天用 stream。
"""
from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from app.config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """全局单例：SDK 内部自带 httpx 连接池，进程内复用一个客户端。"""
    global _client
    if _client is None:
        _client = OpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)
    return _client


def chat_sync(messages: list[dict], temperature: float = 0.7, max_tokens: int | None = None,
              timeout: float = 15.0) -> str:
    """一次性对话：返回完整文本（query 改写等短任务用）。

    temperature=0.1 用于改写（要确定性），=0.7 用于生成（要多样性）。
    timeout 默认 15s：短任务理应很快返回，超时说明 API 异常，不应让用户一直等。
    """
    resp = get_client().chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict], temperature: float = 0.7, timeout: float = 60.0) -> Iterator[str]:
    """流式对话：逐段 yield 文本增量（SSE 打字机效果的数据源）。

    面试点：为什么流式？DeepSeek 生成 200 字要一段时间，流式输出可以让用户拥有更好的体验；
    stream=True 返回迭代器，每 chunk 取 delta.content 逐段下发。
    timeout 默认 60s：长回答可能需要较久，但超过 60s 说明异常，不应无限等。
    """
    stream = get_client().chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=temperature,
        stream=True,
        timeout=timeout,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content