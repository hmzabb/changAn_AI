"""会话存储：进程内内存 dict，每会话保留近 3 轮（6 条消息）。

为什么内存（面试点）：个人项目单进程部署，内存 dict 最简单可靠；
如果将来多实例/重启要保留会话，把本模块换成 Redis 即可（Java 端正好有 Redis）：
key 设计 session:{id} → JSON 数组，TTL 30 分钟，读写加锁。接口不变，
上层 rag_service/chat 无感知——这就是"依赖收敛在 service 层"的收益。
"""
from __future__ import annotations

import threading
from collections import deque

MAX_ROUNDS = 3  # 1 轮 = 用户 + 助手各 1 条；deque 超长自动丢最旧的

_lock = threading.Lock()
_sessions: dict[str, deque] = {}


def get_history(session_id: str) -> list[dict]:
    """按时间顺序返回 [{role, content}]（query 改写要用）。"""
    with _lock:
        return list(_sessions.get(session_id, []))


def append(session_id: str, role: str, content: str) -> None:
    with _lock:
        _sessions.setdefault(session_id, deque(maxlen=MAX_ROUNDS * 2)).append(
            {"role": role, "content": content}
        )
