"""意图分类Prompt模板配置。

设计原则：
- Prompt与逻辑分离，便于A/B测试和迭代
- 支持多版本管理（V4, V5...）
- 便于独立调整和效果对比
"""


class IntentPrompts:
    """意图分类相关Prompt模板集中管理。"""

    # =========================================================================
    # V4.0 LLM意图分类Prompt（当前生产版本）
    # =========================================================================
    INTENT_CLASSIFICATION_V4 = """你是一个高级意图识别专家。分析用户的真实意图，决定应该走哪条处理链路。

【用户问题】
{question}

【已提取的意图特征】
- 时间意图: {time_intent}
  (realtime=现在/今天/实时, historical=去年/历史, undefined=未明确)
- 精确度意图: {precision_intent}
  (exact=精确/准确/具体, approximate=大概/大约/左右, undefined=未明确)
- 数据类型: {data_type}
  (pricing=价格, rating=评价, info=介绍/攻略, operation=营业时间等)
- 主体类型: {subject_type}
  (attraction=景点/博物馆, shop=店铺/餐厅, food=美食, activity=活动)

【决策原则】（重要！请严格遵循）
1. **实时性优先**：
   - 用户要"现在/今天/当前"的数据 → task（agent）
   - 即使知识库可能有，也要查实时数据

2. **参考性次之**：
   - 用户要"大概/大约/左右"的信息 → knowledge（rag）
   - 从攻略、经验中找估算值即可

3. **精确度区分**：
   - "标准价/官方价/定价" → knowledge（rag）权威固定信息
   - "现在多少钱/实时价格" → task（agent）可能变动

4. **主体区分**：
   - 店铺相关动态数据（价格/评分/营业时间）→ task（agent）
   - 景点文化/历史/介绍 → knowledge（rag）

5. **数据新鲜度**：
   - 价格可能变化 → 优先task
   - 历史/文化不变 → knowledge

【输出格式】（严格JSON，不要其他内容）
{{
  "intent": "task" | "knowledge",
  "confidence": 0.0-1.0,
  "reason": "详细理由（结合上述特征分析为什么这样判断）",
  "suggested_action": "agent" | "rag"
}}"""

    @classmethod
    def get_classification_prompt(cls, version: str = "v4") -> str:
        """获取指定版本的分类Prompt。

        Args:
            version: 版本号（默认v4）

        Returns:
            str: Prompt模板字符串
        """
        prompts = {
            "v4": cls.INTENT_CLASSIFICATION_V4,
        }
        return prompts.get(version.lower(), cls.INTENT_CLASSIFICATION_V4)


# 全局单例
_prompts_instance: IntentPrompts = None


def get_intent_prompts() -> IntentPrompts:
    """获取Prompt模板单例。"""
    global _prompts_instance
    if _prompts_instance is None:
        _prompts_instance = IntentPrompts()
    return _prompts_instance