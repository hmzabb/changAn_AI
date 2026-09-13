"""会话存储：Redis 持久化，支持多实例共享、重启不丢失。

面试点（三层设计）：
- 连接池：全局 ConnectionPool 复用连接，避免每次读写都建连/拆连；
- Pipeline：get → 修改 → set 是非原子的，但本项目消息量小（6 条），
  用 pipeline 打包 set + expire + ltrim 即可，无需 Lua 脚本；
- 降级：Redis 不可用时返回空历史（用户重新提问），不阻塞整个服务。

升级前（内存 dict）：
  优点：零依赖，微秒级；缺点：重启丢失，多实例不共享。
升级后（Redis）：
  优点：持久化，多实例共享，TTL 自动过期；缺点：依赖 Redis 进程。
接口不变——上层 chat.py 零改动。
"""
from __future__ import annotations

import json

import redis
from redis.exceptions import RedisError

from app.config import settings

MAX_ROUNDS = 3  # 1 轮 = 用户 + 助手各 1 条

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