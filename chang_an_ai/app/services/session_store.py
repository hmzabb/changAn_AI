"""会话存储：Redis 持久化，支持多实例共享、重启不丢失。

- 连接池：全局 ConnectionPool 复用连接，避免每次读写都建连/拆连；
- Pipeline：get → 修改 → set 是非原子的，但本项目消息量小（6 条），
  用 pipeline 打包 set + expire + ltrim 即可，无需 Lua 脚本；
- 降级：Redis 不可用时返回空历史（用户重新提问），不阻塞整个服务。
- 摘要记忆：超过阈值时自动调用 LLM 压缩历史对话，保留关键语义。

升级前（内存 dict）：
  优点：零依赖，微秒级；缺点：重启丢失，多实例不共享。
升级后（Redis）：
  优点：持久化，多实例共享，TTL 自动过期；缺点：依赖 Redis 进程。
接口不变——上层 chat.py 零改动（append_with_summary 向后兼容 append）。
"""
from __future__ import annotations

import json
import logging

import redis
from redis.exceptions import RedisError

from app.config import settings
from app.services.llm import chat_sync

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3  # 1 轮 = 用户 + 助手各 1 条（原始滑动窗口）

SUMMARY_PROMPT = """请将以下对话压缩成关键信息点（JSON格式）：
{messages}

要求（严格按此格式输出，不要输出其他内容）：
{{
  "user_preferences": ["用户偏好1", "偏好2"],
  "constraints": ["约束条件1", "约束条件2"],
  "decisions": ["已确定决策1", "决策2"],
  "key_entities": ["关键实体1", "实体2"]
}}

控制在150字以内，只提取明确提及的信息，不要推测。"""

# 连接池：全局复用，线程安全
_pool: redis.ConnectionPool | None = None


def _get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            max_connections=10,
            decode_responses=True,  # 自动 decode 为 str，不用手动 .decode()
        )
    return _pool


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def get_history(session_id: str) -> list[dict]:
    """按时间顺序返回 [{role, content}]。
    
    Redis 不可用时返回空列表——让用户重新提问，不阻塞服务。
    """
    try:
        r = redis.Redis(connection_pool=_get_pool())
        raw = r.lrange(_key(session_id), 0, -1)
        return [json.loads(item) for item in raw]
    except RedisError:
        return []


def append(session_id: str, role: str, content: str) -> None:
    """追加消息，保持最近 MAX_ROUNDS 轮。

    Redis List 操作：
    - RPUSH 追加到队尾
    - LTRIM 保留最后 N 条（利用 Redis 的 O(1) 裁剪）
    - EXPIRE 刷新 TTL
    - Pipeline 打包三条命令，一次网络往返
    """
    try:
        r = redis.Redis(connection_pool=_get_pool())
        pipe = r.pipeline()
        key = _key(session_id)
        item = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        pipe.rpush(key, item)
        pipe.ltrim(key, -(MAX_ROUNDS * 2), -1)  # 保留最后 6 条
        pipe.expire(key, settings.redis_session_ttl_seconds)
        pipe.execute()
    except RedisError:
        pass  # Redis 不可用时静默丢弃，不阻塞主流程


async def append_with_summary(session_id: str, role: str, content: str) -> None:
    """带智能摘要的记忆管理（向后兼容原append接口）。

    工作流程（修正版 - 解决LTRIM过早裁剪问题）：
    1. 先追加消息到Redis（但不立即LTRIM）
    2. 检查是否启用摘要功能且超过阈值
    3a. 如果需要摘要：调用LLM生成结构化摘要 → 替换为[摘要+最近2轮]
    3b. 如果不需要摘要：执行LTRIM保留最近MAX_ROUNDS轮（与原append行为一致）
    4. 摘要失败时降级到原始滑动窗口，不影响主流程

    Args:
        session_id: 会话ID
        role: 消息角色（user/assistant）
        content: 消息内容
    """
    try:
        r = redis.Redis(connection_pool=_get_pool())
        pipe = r.pipeline()
        key = _key(session_id)
        item = json.dumps({"role": role, "content": content}, ensure_ascii=False)

        pipe.rpush(key, item)
        pipe.expire(key, settings.redis_session_ttl_seconds)
        pipe.execute()

        if not settings.summary_enabled:
            pipe = r.pipeline()
            pipe.ltrim(key, -(MAX_ROUNDS * 2), -1)
            pipe.execute()
            return

        history = get_history(session_id)
        total_messages = len(history)

        if total_messages <= settings.summary_max_rounds * 2:
            pipe = r.pipeline()
            pipe.ltrim(key, -(MAX_ROUNDS * 2), -1)
            pipe.execute()
            return

        if total_messages % 2 != 0:
            pipe = r.pipeline()
            pipe.ltrim(key, -(MAX_ROUNDS * 2), -1)
            pipe.execute()
            return

        messages_to_summarize = history[:-2]
        prompt = SUMMARY_PROMPT.format(
            messages="\n".join([f'{m["role"]}: {m["content"]}' for m in messages_to_summarize])
        )

        summary_text = chat_sync(
            messages=[{"role": "system", "content": "你是对话摘要助手，只输出JSON。"},
                     {"role": "user", "content": prompt}],
            temperature=settings.summary_model_temperature,
            max_tokens=300,
            timeout=10.0
        )

        try:
            summary_dict = json.loads(summary_text)
            summary_formatted = (
                f"[历史对话摘要]\n"
                f"- 用户偏好: {', '.join(summary_dict.get('user_preferences', []))}\n"
                f"- 约束条件: {', '.join(summary_dict.get('constraints', []))}\n"
                f"- 已做决策: {', '.join(summary_dict.get('decisions', []))}\n"
                f"- 关键信息: {', '.join(summary_dict.get('key_entities', []))}"
            )
        except json.JSONDecodeError:
            summary_formatted = f"[历史对话摘要]\n{summary_text}"

        pipe = r.pipeline()
        pipe.delete(key)

        summary_item = json.dumps({
            "role": "system",
            "content": summary_formatted
        }, ensure_ascii=False)
        pipe.rpush(key, summary_item)

        recent_msgs = history[-2:]
        for msg in recent_msgs:
            item = json.dumps(msg, ensure_ascii=False)
            pipe.rpush(key, item)

        pipe.expire(key, settings.redis_session_ttl_seconds)
        pipe.execute()

        logger.info(f"Session {session_id}: 已生成摘要，历史从{total_messages}条压缩到3条")

    except Exception as e:
        logger.warning(f"Session {session_id}: 摘要生成失败，降级到滑动窗口模式: {e}")
        try:
            r = redis.Redis(connection_pool=_get_pool())
            r.ltrim(_key(session_id), -(MAX_ROUNDS * 2), -1)
        except Exception:
            pass