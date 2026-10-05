"""路由监控模块：收集路由决策的业务维度数据，用于诊断和优化。

职责划分：
- Prometheus：HTTP 层指标（QPS/P99延迟/错误率）—— 运维视角
- RoutingMonitor：业务诊断（关键词命中/冲突检测/分布）—— 算法/产品视角

功能：
1. 记录每次路由决策的维度信息
2. 统计路由分布（Agent vs RAG比例）
3. 检测潜在的错误路由模式
4. 导出诊断报告
"""

import json
import threading
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class RoutingMonitor:
    """路由监控器（线程安全）—— 专注业务诊断维度，延迟统计交给 Prometheus。"""

    def __init__(self, max_records: int = 10000):
        self._records: List[Dict] = []
        self._lock = threading.Lock()
        self._max_records = max_records

        # 统计计数器
        self._stats = {
            "total_routes": 0,
            "agent_routes": 0,
            "rag_routes": 0,
            "conflict_detected": 0,
        }

    def record(
        self,
        route_result: str,
        has_agent: bool = False,
        has_rag: bool = False,
        conflict: str = "",
    ):
        """记录一次路由决策（精简版：仅业务诊断维度）

        Args:
            route_result: 路由结果 ("agent" | "rag")
            has_agent: 是否命中 Agent 关键词
            has_rag: 是否命中 RAG 关键词
            conflict: 冲突解决方式（空字符串表示无冲突）
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "route_result": route_result,
            "has_agent_keywords": has_agent,
            "has_rag_keywords": has_rag,
            "conflict_detected": bool(conflict),
            "conflict_resolution": conflict,
        }

        with self._lock:
            self._records.append(record)

            self._stats["total_routes"] += 1
            if route_result == "agent":
                self._stats["agent_routes"] += 1
            else:
                self._stats["rag_routes"] += 1

            if conflict:
                self._stats["conflict_detected"] += 1

            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

    def get_stats(self, time_range_hours: int = 1) -> Dict:
        """获取路由统计数据（业务维度）

        Args:
            time_range_hours: 统计时间范围（小时）
        """
        with self._lock:
            cutoff_time = datetime.now() - timedelta(hours=time_range_hours)
            recent = [
                r for r in self._records
                if datetime.fromisoformat(r["timestamp"]) >= cutoff_time
            ]

            if not recent:
                return {
                    "time_range_hours": time_range_hours,
                    "total_routes": 0,
                    "message": "暂无数据",
                }

            total = len(recent)
            agent_count = sum(1 for r in recent if r["route_result"] == "agent")
            rag_count = total - agent_count
            conflict_count = sum(1 for r in recent if r["conflict_detected"])

            return {
                "time_range_hours": time_range_hours,
                "generated_at": datetime.now().isoformat(),
                # 路由分布
                "total_routes": total,
                "agent_routes": agent_count,
                "rag_routes": rag_count,
                "agent_percentage": round(agent_count / total * 100, 1),
                "rag_percentage": round(rag_count / total * 100, 1),
                # 冲突检测
                "conflicts_detected": conflict_count,
                "conflict_percentage": round(conflict_count / total * 100, 1),
                # 关键词命中分布
                "agent_only_hits": sum(
                    1 for r in recent if r["has_agent_keywords"] and not r["has_rag_keywords"]
                ),
                "rag_only_hits": sum(
                    1 for r in recent if r["has_rag_keywords"] and not r["has_agent_keywords"]
                ),
                "both_hits": sum(
                    1 for r in recent if r["has_agent_keywords"] and r["has_rag_keywords"]
                ),
                "no_hits": sum(
                    1 for r in recent
                    if not r["has_agent_keywords"] and not r["has_rag_keywords"]
                ),
            }

    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        """获取最近的潜在错误路由案例（用于人工审核关键词覆盖）

        Args:
            limit: 返回的最大数量
        """
        with self._lock:
            suspicious = []
            for r in reversed(self._records[-1000:]):
                reasons = []

                # 冲突案例优先
                if r["conflict_detected"]:
                    reasons.append("意图冲突")
                # 无特征命中（可能是关键词遗漏）
                if not r["has_agent_keywords"] and not r["has_rag_keywords"]:
                    reasons.append("无特征命中→兜底RAG")

                if reasons:
                    suspicious.append({**r, "suspicion_reasons": reasons})

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
                "recent_records": self._records[-100:],
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
        """清理旧记录"""
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