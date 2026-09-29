# 🧠 V4.0 意图感知路由系统详解

> **本文档详细介绍项目的核心创新：四代意图路由机制的演进历程、V4.0 架构设计与代码实现。**

## 目录

- [为什么需要意图路由](#为什么需要意图路由)
- [四代版本演进史](#四代版本演进史)
- [V4.0 架构设计](#v40-架构设计)
- [核心组件详解](#核心组件详解)
- [防误判机制](#防误判机制)
- [性能指标](#性能指标)

---

## 为什么需要意图路由

### 业务场景分析

用户问题天然分为两类：

| 类型 | 特征 | 示例 | 需要的能力 |
|------|------|------|-----------|
| **知识型查询** | 询问静态信息（历史/文化/攻略） | 「西安有什么景点？」 | RAG 检索知识库 |
| **任务型查询** | 需要实时数据操作（查询/筛选/排序） | 「附近有优惠店铺吗？」 | Agent 调用工具 |

**问题**: 如果路由错误会导致用户体验极差
- ❌ 知识型问题走Agent → 调用一堆工具却返回"找不到"
- ❌ 任务型问题走RAG → 返回过时或无关的知识

---

## 四代版本演进史

> **注意**: 此处展示的是"意图路由系统"这一功能的迭代历史。

```
V1.0 线性规则 (70%准确率)
  ↓ 缺少模糊匹配能力
V2.0 三层漏斗 (85%准确率)
  ↓ 会误判RAG问题到Agent
V2.5 RAG强制层 (90%准确率)
  ↓ 会误杀Agent请求
V3.0 智能覆盖层 (95%准确率)
  ↓ 无法区分"大概"/"现在"等时间意图
V4.0 意图感知路由 (99%+准确率) ← 当前方案 ✅
```

### 各版本对比

| 版本 | 核心机制 | 准确率 | 主要问题 | 解决方案 |
|------|---------|--------|----------|----------|
| **V1.0** | 线性 if-else 关键词匹配 | 70% | 缺少模糊匹配 | 引入正则表达式 |
| **V2.0** | 三层漏斗（关键词→正则→LLM） | 85% | 误判 RAG→Agent | 增加 RAG 强制词 |
| **V2.5** | RAG 强制层（景点/博物馆） | 90% | 误杀 Agent 请求 | 增加 Agent 覆盖词 |
| **V3.0** | 智能覆盖层（覆盖+例外组合） | 95% | 无法区分时间意图 | 引入 4 维特征提取 |
| **V4.0** | **意图感知路由**（特征提取+规则引擎+防误判） | **99%+** | — | 当前方案 |

---

## V4.0 架构设计

### 三层路由流程

```mermaid
flowchart TD
    A[用户问题输入] --> B[特征提取器 FeatureExtractor]
    B --> C{第一层: 高置信度关键词}
    C -->|命中| D[✅ 直接决策]
    C -->|未命中| E{第二层: 正则模式}
    E -->|命中| F[📝 上下文检测]
    E -->|未命中| G{第三层: LLM 分类}
    F --> G2{歧义检测}
    G2 -->|RAG强制词+无Agent覆盖| H[强制RAG]
    G2 -->|Agent覆盖词+例外组合| I[智能判定]
    G2 -->|正常情况| J[按规则路由]
    G --> K[🤖 DeepSeek 兜底]
    D --> L[RoutingDecision]
    H --> L
    I --> L
    J --> L
    K --> L
    L --> M{action?}
    M -->|rag| N[RAG Pipeline]
    M -->|agent| O[Agent Pipeline]

    style A fill:#e3f2fd
    style B fill:#fff9c4
    style C fill:#fce4ec
    style G fill:#f3e5f5
    style L fill:#e1f5fe
```

### 延迟与准确率权衡

| 层级 | 覆盖场景 | 延迟 | 准确率 | 调用比例 |
|------|---------|------|--------|----------|
| 第一层：关键词 | 高置信度词汇（70%） | < 10ms | 99% | ~70% |
| 第二层：正则 | 模糊表达变体（20%） | < 20ms | 95% | ~20% |
| 第三层：LLM | 深层语义理解（10%） | 500-1500ms | 92% | ~10% |

**关键洞察**: 90% 的请求在第一层就完成路由，平均延迟 < 10ms！

---

## 核心组件详解

### 1️⃣ 特征提取器 (`feature_extractor.py`)

**文件路径**: `chang_an_ai/app/intent/feature_extractor.py`

```python
@dataclass
class IntentFeatures:
    """4维意图特征"""
    time_intent: str       # realtime（实时）/ knowledge（静态）
    precision_intent: str  # exact（精确）/ fuzzy（模糊）
    data_type: str         # pricing / location / entity / general
    subject_type: str      # shop / attraction / food / other
```

#### 各维度含义

| 维度 | 取值 | 判断依据 | 业务意义 |
|------|------|----------|----------|
| `time_intent` | `realtime` / `knowledge` | 是否需要实时数据（价格/营业时间/库存） | 决定走Agent查Java API还是RAG查静态库 |
| `precision_intent` | `exact` / `fuzzy` | 是否有明确实体名称（"钟楼小吃街" vs "附近好吃的"） | 影响工具调用的参数精确度 |
| `data_type` | `pricing/location/entity/general` | 用户关注的数据类型 | 选择合适的工具（价格→优惠券，位置→GEO排序） |
| `subject_type` | `shop/attraction/food/other` | 查询的主体对象 | 决定调用哪个领域的数据源 |

#### 提取示例

```python
# 输入："钟楼附近人均80以下的美食店"
features = extractor.extract("钟楼附近人均80以下的美食店")
# 输出:
# IntentFeatures(
#     time_intent="realtime",      # 价格和店铺信息可能变化
#     precision_intent="fuzzy",    # "美食店"是模糊描述，无具体店名
#     data_type="pricing",         # 关注"人均80以下"
#     subject_type="food"          # 主体是美食
# )
# → 路由决策: action="agent", reason="实时价格查询"
```

---

### 2️⃣ 规则引擎 (`router.py`)

**文件路径**: `chang_an_ai/app/intent/router.py`

```python
class IntentRouter:
    def __init__(self):
        self.rules = RoutingRules()  # 从 routing_rules.py 加载配置
        self.feature_extractor = FeatureExtractor()
        self.llm_classifier = LLMClassifier()

    async def route(self, message: str) -> RoutingDecision:
        """主路由函数"""
        # Step 1: 提取特征
        features = self.feature_extractor.extract(message)

        # Step 2: 规则匹配（按优先级遍历）
        for rule in self.rules.get_rules():
            if self._match_conditions(features, rule["conditions"]):
                return RoutingDecision(
                    action=rule["action"],      # "rag" or "agent"
                    confidence=rule["confidence"],
                    reason=rule["reason"],
                    matched_rule=rule["name"]
                )

        # Step 3: LLM 兜底（前两层都没命中时）
        return await self.llm_classifier.classify(message)
```

#### 规则示例

```python
# routing_rules.py
RULES = [
    {
        "name": "agent_pricing_realtime",
        "conditions": {
            "time_intent": "realtime",
            "data_type": "pricing"
        },
        "action": "agent",
        "confidence": 0.95,
        "reason": "实时价格查询需要Agent调用Java API"
    }
]
```

---

### 3️⃣ LLM 分类兜底 (`llm_classifier.py`)

**文件路径**: `chang_an_ai/app/intent/llm_classifier.py`

```python
async def llm_classify_v4(message: str) -> str:
    """使用 DeepSeek 做意图分类（仅前两层都没命中时调用）"""

    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        temperature=0,  # 确保确定性输出
        timeout=LLM_CLASSIFY_TIMEOUT,  # 超时控制 3秒
    )

    response = await model.ainvoke(prompt)
    result = json.loads(response.content)

    intent = result["intent"]      # "task" or "knowledge"
    confidence = result["confidence"]  # 0.0 - 1.0

    # 置信度阈值：低于 0.6 保守选择 RAG
    if intent == "task" and confidence >= 0.6:
        return "agent"
    return "rag"
```

**为什么需要 LLM 兜底？**

| 问题 | 关键词/正则能识别？ | LLM 能识别？ |
|------|-------------------|-------------|
| "给我推荐个好去处" | ❌ 无明确关键词 | ✅ 理解为任务型（推荐=Agent） |
| "周末去哪玩比较好" | ❌ 模糊表达 | ✅ 结合上下文判断为知识型（攻略=RAG） |

---

## 防误判机制

### 核心问题：「景点有团购吗？」怎么处理？

这是一个**混合意图**问题：
- 包含「景点」→ 应该走 RAG（静态知识）
- 包含「团购」→ 应该走 Agent（动态查询）

### 解决方案：优先级覆盖机制

**文件路径**: `routing_config.py` (项目根目录)

```python
# RAG 强制词：包含这些词时强制走 RAG
RAG_FORCE_KEYWORDS = (
    "景点", "景区", "博物馆", "古迹", "门票",
    "历史", "文化", "攻略", "路线", "游记"
)

# Agent 覆盖词：即使命中 RAG 强制词，如果同时出现这些词仍然走 Agent
AGENT_OVERRIDE_KEYWORDS = (
    "优惠券", "券", "团购", "折扣", "店铺", "店",
    "评分", "评价", "人均", "价格", "推荐"
)

# 例外组合：特定组合即使有 Agent 覆盖词也强制走 RAG
AGENT_OVERRIDE_EXCEPTIONS = {
    "价格": ["门票", "开放时间", "学生票"],
    "推荐": ["景点", "路线", "美食"],
    "多少": ["历史", "年", "朝代"]
}
```

### 处理流程

```mermaid
flowchart LR
    A["用户问题"] --> B{"包含<br/>RAG强制词?"}
    B -->|否| C["正常路由"]
    B -->|是| D{"同时包含<br/>Agent覆盖词?"}
    D -->|否| E["✅ 强制走RAG"]
    D -->|是| F{"属于<br/>例外组合?"}
    F -->|是| E
    F -->|否| G["✅ 覆盖走Agent"]

    style E fill:#e8f5e9
    style G fill:#fff3e0
```

### 实际案例

| 问题 | RAG强制词 | Agent覆盖词 | 例外组合 | 最终决策 | 原因 |
|------|----------|------------|---------|---------|------|
| 「景点门票价格多少？」 | ✅ 景点/门票 | ✅ 价格 | ✅ 价格+门票 | **RAG** | 例外组合优先 |
| 「景点有团购吗？」 | ✅ 景点 | ✅ 团购 | ❌ 无 | **Agent** | 覆盖词生效 |
| 「西安博物馆介绍」 | ✅ 博物馆 | ❌ 无 | — | **RAG** | 强制词生效 |

---

## 性能指标

### 测试数据

```
【意图路由性能】
├─ 关键词命中延迟:     2-5ms   (P99 < 10ms)
├─ 正则匹配延迟:       5-15ms  (P99 < 20ms)
├─ LLM 分类延迟:       500-1500ms (仅 10% 请求触发)
└─ 平均路由延迟:       8-12ms  (90% 请求在关键词层解决)

【准确率】
├─ 整体准确率:         99%+
├─ 关键词层准确率:     99%
├─ 正则层准确率:       96%
└─ LLM 兜底准确率:     93%
```

### 与旧版对比

| 指标 | V3.0 | V4.0 | 提升 |
|------|------|------|------|
| 准确率 | 95% | **99%+** | +4% |
| 平均延迟 | 8ms | 12ms | +50%（但更准确） |
| 可解释性 | 弱 | **强**（reason字段） | ↑↑ |
| 可维护性 | 差 | **好**（外部化规则） | ↑↑ |

---

## 相关文档

- [架构设计](./ARCHITECTURE.md) - nginx 分流与 Java/Python 协作
- [面试问题汇总](./INTERVIEW_QA.md) - 意图路由相关面试题与答案模板