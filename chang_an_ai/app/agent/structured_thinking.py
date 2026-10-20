"""结构化思考模块（第六层防御）：强制Agent在调用工具前进行结构化推理。

设计要点：
- 使用Pydantic模型定义强制思考步骤
- 在工具调用前要求LLM输出结构化思考结果
- 基于思考结果验证工具选择的合理性
- 可选启用/禁用（默认禁用，避免增加延迟）

使用方式：
    from app.agent.structured_thinking import StructuredThinking, AgentThink

    # 在agent_node中启用
    thinking = await StructuredThinking.force_think(model, user_query, available_tools)
    if not thinking.is_valid:
        # 思考不合理，可以拒绝或修正
        pass

性能影响：
- 每次工具调用前增加1次LLM调用（约200-500ms）
- 适合对准确性要求极高的场景
- 生产环境建议根据需要动态开启/关闭
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class AgentThink(BaseModel):
    """强制Agent在调用工具前的结构化思考。

    这个模型确保LLM在调用工具前必须：
    1. 明确分析用户意图
    2. 列出需要的工具及理由
    3. 验证参数的合理性
    4. 确认是否与西安文旅相关
    """

    user_intent: str = Field(
        ...,
        description="用户查询的真实意图（一句话概括）",
        min_length=5,
        max_length=200
    )

    needed_tools: List[str] = Field(
        ...,
        description="需要调用的工具名称列表（从可用工具中选择）",
        min_items=0,
        max_items=6
    )

    tool_reasoning: Dict[str, str] = Field(
        default_factory=dict,
        description="每个工具的选择理由 {tool_name: reason}"
    )

    is_travel_related: bool = Field(
        ...,
        description="是否与西安文旅相关（True/False）"
    )

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="对这个工具选择方案的置信度（0-1）"
    )

    potential_issues: List[str] = Field(
        default_factory=list,
        description="潜在问题或风险（如参数可能不合法、可能需要多轮调用等）"
    )

    @field_validator('needed_tools')
    @classmethod
    def validate_tools(cls, v: List[str]) -> List[str]:
        """验证工具名称是否在允许列表中。"""
        valid_tools = {
            "search_shops_by_name",
            "list_shops_by_type",
            "get_shop_detail",
            "list_vouchers",
            "search_blogs",
            "search_knowledge",
        }
        for tool in v:
            if tool not in valid_tools:
                raise ValueError(f"无效的工具名: {tool}，可用工具: {valid_tools}")
        return v

    @property
    def is_valid(self) -> bool:
        """快速验证思考结果是否合理。"""
        # 必须与西安文旅相关
        if not self.is_travel_related:
            return False

        # 如果需要工具，必须有理由
        if self.needed_tools and not self.tool_reasoning:
            return False

        # 置信度不能太低
        if self.confidence < 0.3:
            return False

        # 工具数量合理（最多6个）
        if len(self.needed_tools) > 6:
            return False

        return True

    def to_prompt_hint(self) -> str:
        """将思考结果转换为提示信息，可注入到后续LLM调用中。"""
        hint_parts = [
            f"📝 意图分析: {self.user_intent}",
            f"🔧 计划调用工具: {', '.join(self.needed_tools) or '无'}",
        ]

        if self.tool_reasoning:
            for tool, reason in self.tool_reasoning.items():
                hint_parts.append(f"   → {tool}: {reason}")

        if self.potential_issues:
            hint_parts.append(f"⚠️ 注意事项: {'; '.join(self.potential_issues)}")

        return "\n".join(hint_parts)


class StructuredThinking:
    """结构化思考执行器（第六层防御）。

    功能：
    1. 强制LLM在调用工具前进行结构化思考
    2. 验证思考结果的合理性
    3. 基于思考结果生成提示增强
    4. 统计思考质量指标
    """

    _enabled: bool = False  # 默认禁用（避免性能开销）
    _stats: Dict[str, Any] = {
        "total_thinks": 0,
        "valid_thinks": 0,
        "invalid_thinks": 0,
        "avg_confidence": 0.0,
    }

    SYSTEM_PROMPT_FOR_THINKING = """你是一个智能助手的前置思考模块。请分析用户查询并输出结构化的思考结果。

【任务】
分析用户查询，决定是否需要调用工具以及调用哪些工具。

【输出格式】
严格按照JSON格式输出，包含以下字段：
{
    "user_intent": "用户意图的一句话概括",
    "needed_tools": ["tool1", "tool2"],
    "tool_reasoning": {"tool1": "为什么选这个工具", "tool2": "理由"},
    "is_travel_related": true/false,
    "confidence": 0.9,
    "potential_issues": ["潜在问题1", "潜在问题2"]
}

【可用工具】
- search_shops_by_name: 按店铺名搜索
- list_shops_by_type: 按分类查店铺
- get_shop_detail: 查店铺详情（需要shop_id）
- list_vouchers: 查优惠券（需要shop_id）
- search_blogs: 搜索探店笔记/评价
- search_knowledge: 搜索西安文旅知识

【判断规则】
1. is_travel_related: 只有与西安旅游、美食、景点、攻略相关的才为true
2. needed_tools: 根据用户需求选择最合适的工具组合
3. confidence: 对自己判断的信心程度（0-1）
4. potential_issues: 可能的问题（如缺少必要参数、可能返回空结果等）

【重要】只输出JSON，不要输出其他内容！"""

    USER_PROMPT_TEMPLATE = """用户查询：{query}

请分析这个查询并输出结构化思考结果（JSON格式）："""

    @classmethod
    def enable(cls) -> None:
        """启用结构化思考。"""
        cls._enabled = True
        logger.info("[StructuredThinking] 已启用")

    @classmethod
    def disable(cls) -> None:
        """禁用结构化思考。"""
        cls._enabled = False
        logger.info("[StructuredThinking] 已禁用")

    @classmethod
    def is_enabled(cls) -> bool:
        """检查是否已启用。"""
        return cls._enabled

    @classmethod
    async def force_think(
        cls,
        model: Any,
        user_query: str,
        available_tools: Optional[List[str]] = None
    ) -> Optional[AgentThink]:
        """强制执行结构化思考。

        Args:
            model: LLM模型实例（支持ainvoke方法）
            user_query: 用户原始查询
            available_tools: 可用工具列表（可选，用于验证）

        Returns:
            AgentThink对象（如果成功），或None（如果失败/未启用）
        """
        if not cls._enabled:
            logger.debug("[StructuredThinking] 未启用，跳过")
            return None

        try:
            cls._stats["total_thinks"] += 1

            # 构建提示
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = [
                SystemMessage(content=cls.SYSTEM_PROMPT_FOR_THINKING),
                HumanMessage(content=cls.USER_PROMPT_TEMPLATE.format(query=user_query))
            ]

            # 调用LLM进行思考
            response = await model.ainvoke(messages)

            # 解析响应
            content = response.content if hasattr(response, 'content') else str(response)

            # 尝试提取JSON
            json_str = cls._extract_json(content)
            if not json_str:
                logger.warning(f"[StructuredThinking] 无法从响应中提取JSON: {content[:100]}")
                cls._stats["invalid_thinks"] += 1
                return None

            # 解析为AgentThink
            think_data = json.loads(json_str)
            think = AgentThink(**think_data)

            # 更新统计
            if think.is_valid:
                cls._stats["valid_thinks"] += 1
                cls._stats["avg_confidence"] = (
                    (cls._stats["avg_confidence"] * (cls._stats["valid_thinks"] - 1) + think.confidence)
                    / cls._stats["valid_thinks"]
                )
                logger.info(
                    f"[StructuredThinking] 思考完成 | "
                    f"意图: {think.user_intent[:50]}... | "
                    f"工具: {think.needed_tools} | "
                    f"置信度: {think.confidence:.2f}"
                )
            else:
                cls._stats["invalid_thinks"] += 1
                logger.warning(
                    f"[StructuredThinking] 思考结果无效 | "
                    f"意图: {think.user_intent[:50]}..."
                )

            return think

        except Exception as e:
            logger.error(f"[StructuredThinking] 思考过程异常: {e}", exc_info=True)
            cls._stats["invalid_thinks"] += 1
            return None

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """从文本中提取JSON字符串。"""
        import re

        # 尝试直接解析
        try:
            json.loads(text)
            return text
        except (json.JSONDecodeError, TypeError):
            pass

        # 尝试查找JSON块
        patterns = [
            r'```json\s*(.*?)\s*```',  # markdown代码块
            r'```\s*(\{.*?\})\s*```',   # 通用代码块
            r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})',  # 嵌套JSON
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                try:
                    json.loads(json_str)
                    return json_str
                except (json.JSONDecodeError, TypeError):
                    continue

        return None

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """获取统计信息。"""
        return {
            **cls._stats,
            "enabled": cls._enabled,
        }

    @classmethod
    def reset_stats(cls) -> None:
        """重置统计数据（测试用）。"""
        cls._stats = {
            "total_thinks": 0,
            "valid_thinks": 0,
            "invalid_thinks": 0,
            "avg_confidence": 0.0,
        }
        logger.info("[StructuredThinking] 统计数据已重置")


# ==================== 便捷函数 ====================

async def structured_think_if_enabled(
    model: Any,
    user_query: str
) -> Optional[AgentThink]:
    """如果启用了结构化思考则执行，否则跳过。

    Args:
        model: LLM模型实例
        user_query: 用户查询

    Returns:
        AgentThink对象或None
    """
    return await StructuredThinking.force_think(model, user_query)