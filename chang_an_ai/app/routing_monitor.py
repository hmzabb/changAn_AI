"""路由监控模块：用于收集和分析路由准确率。

功能：
1. 记录每次路由决策的详细信息
2. 统计路由分布（Agent vs RAG比例）
3. 监控延迟性能
4. 检测异常模式（如某类问题频繁错误路由）
5. 生成监控报告

使用方式：
- 自动记录：在_route()函数中自动调用
- 手动分析：调用 get_routing_stats() 获取统计数据
- 导出报告：调用 export_report() 导出JSON/CSV
"""

import time
import json
import threading
from typing import Dict, List, Optional, Any
from collections import defaultdict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class RoutingMonitor:
    """路由监控器（线程安全）"""

    def __init__(self, max_records: int = 10000):
        """
        Args:
            max_records: 最大记录数（防止内存溢出）
        """
        self._records: List[Dict] = []
        self._lock = threading.Lock()
        self._max_records = max_records

        # 统计计数器
        self._stats = {
            "total_routes": 0,
            "agent_routes": 0,
            "rag_routes": 0,
            "conflict_detected": 0,
            "fallback_to_rag": 0,
            "mode_override": 0,
        }

    def record_routing(
        self,
        session_id: str,
        message: str,
        mode: str,
        route_result: str,
        latency_ms: float,
        has_agent_keywords: bool = False,
        has_rag_keywords: bool = False,
        conflict_detected: bool = False,
        conflict_resolution: Optional[str] = None,
    ):
        """记录一次路由决策

        Args:
            session_id: 会话ID
            message: 用户原始消息
            mode: 请求模式（auto/agent/rag）
            route_result: 路由结果（agent/rag）
            latency_ms: 路由延迟（毫秒）
            has_agent_keywords: 是否命中Agent关键词
            has_rag_keywords: 是否命中RAG关键词
            conflict_detected: 是否检测到冲突
            conflict_resolution: 冲突解决方式（如果有的话）
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id[:12],  # 截断保护隐私
            "message_preview": message[:50] + ("..." if len(message) > 50 else ""),
            "message_length": len(message),
            "mode": mode,
            "route_result": route_result,
            "latency_ms": round(latency_ms, 3),
            "has_agent_keywords": has_agent_keywords,
            "has_rag_keywords": has_rag_keywords,
            "conflict_detected": conflict_detected,
            "conflict_resolution": conflict_resolution,
        }

        with self._lock:
            # 添加记录
            self._records.append(record)

            # 更新统计
            self._stats["total_routes"] += 1
            if route_result == "agent":
                self._stats["agent_routes"] += 1
            else:
                self._stats["rag_routes"] += 1

            if conflict_detected:
                self._stats["conflict_detected"] += 1

            if mode != "auto":
                self._stats["mode_override"] += 1

            # 限制记录数量（FIFO淘汰）
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

        # 异步写入日志（避免阻塞主流程）
        logger.debug(
            f"[路由监控] result={route_result}, latency={latency_ms:.3f}ms, "
            f"msg='{message[:30]}...', conflict={conflict_detected}"
        )

    def get_stats(self, time_range_hours: int = 1) -> Dict[str, Any]:
        """获取路由统计数据

        Args:
            time_range_hours: 统计时间范围（小时）

        Returns:
            包含详细统计信息的字典
        """
        with self._lock:
            # 过滤时间范围
            cutoff_time = datetime.now() - timedelta(hours=time_range_hours)
            recent_records = [
                r for r in self._records
                if datetime.fromisoformat(r["timestamp"]) >= cutoff_time
            ]

            if not recent_records:
                return {
                    "time_range_hours": time_range_hours,
                    "total_routes": 0,
                    "message": "暂无数据",
                }

            # 基础统计
            total = len(recent_records)
            agent_count = sum(1 for r in recent_records if r["route_result"] == "agent")
            rag_count = total - agent_count

            # 延迟统计
            latencies = [r["latency_ms"] for r in recent_records]
            latencies.sort()
            p50_idx = total // 2
            p90_idx = int(total * 0.9)
            p99_idx = int(total * 0.99)

            stats = {
                "time_range_hours": time_range_hours,
                "generated_at": datetime.now().isoformat(),

                # 路由分布
                "total_routes": total,
                "agent_routes": agent_count,
                "rag_routes": rag_count,
                "agent_percentage": round(agent_count / total * 100, 1),
                "rag_percentage": round(rag_count / total * 100, 1),

                # 延迟指标
                "latency_avg_ms": round(sum(latencies) / total, 3),
                "latency_p50_ms": round(latencies[p50_idx], 3) if p50_idx < total else 0,
                "latency_p90_ms": round(latencies[p90_idx], 3) if p90_idx < total else 0,
                "latency_p99_ms": round(latencies[p99_idx], 3) if p99_idx < total else 0,
                "latency_min_ms": round(min(latencies), 3),
                "latency_max_ms": round(max(latencies), 3),

                # 冲突检测
                "conflicts_detected": sum(1 for r in recent_records if r["conflict_detected"]),
                "conflict_percentage": round(
                    sum(1 for r in recent_records if r["conflict_detected"]) / total * 100, 1
                ),

                # 模式覆盖
                "mode_overrides": sum(1 for r in recent_records if r["mode"] != "auto"),

                # 消息长度分布
                "avg_message_length": round(
                    sum(r["message_length"] for r in recent_records) / total, 1
                ),
            }

            return stats

    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        """获取最近的潜在错误路由案例

        用于人工审核，找出可能需要优化的关键词

        Args:
            limit: 返回的最大数量

        Returns:
            可能存在问题的路由记录列表
        """
        with self._lock:
            # 筛选可能的问题案例：
            # 1. 高延迟（>5ms）
            # 2. 冲突但解决方式可能不对
            # 3. 无特征词但消息较长（可能是遗漏的关键词）

            suspicious = []
            for r in reversed(self._records[-1000:]):  # 最近1000条
                is_suspicious = False
                reasons = []

                # 高延迟
                if r["latency_ms"] > 5.0:
                    is_suspicious = True
                    reasons.append(f"高延迟({r['latency_ms']}ms)")

                # 长消息但无特征
                if r["message_length"] > 20 and not (r["has_agent_keywords"] or r["has_rag_keywords"]):
                    is_suspicious = True
                    reasons.append("长消息无特征")

                # 冲突案例
                if r["conflict_detected"]:
                    is_suspicious = True
                    reasons.append("意图冲突")

                if is_suspicious:
                    suspicious.append({
                        **r,
                        "suspicion_reasons": reasons,
                    })

                if len(suspicious) >= limit:
                    break

            return suspicious

    def export_report(self, filepath: str, format: str = "json"):
        """导出监控报告

        Args:
            filepath: 输出文件路径
            format: 导出格式（json/csv）
        """
        with self._lock:
            report = {
                "export_time": datetime.now().isoformat(),
                "statistics": self.get_stats(time_range_hours=24),
                "recent_records": self._records[-100:],  # 最近100条
                "suspicious_cases": self.get_recent_errors(limit=50),
            }

        if format == "json":
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"路由监控报告已导出: {filepath}")

        elif format == "csv":
            import csv
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                if not self._records:
                    return
                writer = csv.DictWriter(f, fieldnames=self._records[0].keys())
                writer.writeheader()
                writer.writerows(self._records[-1000:])
            logger.info(f"路由监控数据已导出(CSV): {filepath}")

        else:
            raise ValueError(f"不支持的格式: {format}")

    def clear_old_records(self, hours: int = 24):
        """清理旧记录

        Args:
            hours: 保留最近N小时的数据
        """
        with self._lock:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            self._records = [
                r for r in self._records
                if datetime.fromisoformat(r["timestamp"]) >= cutoff_time
            ]
            logger.info(f"已清理{hours}小时前的旧记录，剩余{len(self._records)}条")


# 全局单例
routing_monitor = RoutingMonitor()


def get_routing_monitor() -> RoutingMonitor:
    """获取全局路由监控器实例"""
    return routing_monitor