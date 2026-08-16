# 长安文旅探店助手 — 项目说明

> Java + Python 双栈 AI 探店平台：以黑马点评为业务底座，叠加大模型能力，实现长安（西安）文旅场景下的智能问答、探店笔记 AI 辅助与智能推荐 Agent。

## 一、项目简介

「长安文旅探店助手」是一个面向西安文旅场景的生活服务平台。用户在平台浏览店铺、领取优惠券、发布探店笔记、点赞评论；在此基础上引入大模型能力：

1. **长安文旅智能问答（RAG）**：以西安文旅知识库 + 平台实时店铺/笔记数据为语料，流式回答"西安三日游怎么安排""回民街有什么好吃的"等问题，回答带引用来源，可一键跳转店铺/笔记详情页。
2. **探店笔记 AI 辅助**：发布笔记时一键生成候选标题、按风格润色正文、检测文本情感（发帖前自检 + 评论区舆情分析）。
3. **探店智能 Agent**：多轮对话中自主调用工具（查店铺/查分类/查详情/查优惠券/查笔记），完成"帮我找钟楼附近人均 80 以下的美食店，再看看有没有优惠券"这类复合任务。

**一句话介绍（简历版）**：在 Spring Boot 点评平台之上，用 FastAPI + LangGraph + ChromaDB 构建了 RAG 智能问答与工具调用 Agent，通过 nginx 网关分流实现 Java/Python 双栈异构集成，SSE 流式输出，回答可溯源引用。

## 二、技术栈总览

| 层 | 技术 |
|---|---|
| 前端 | Vue2 + Element UI + axios（nginx 静态页，8080） |
| 网关 | nginx 1.18（前缀分流：业务 → Java，AI 流式 → Python） |
| Java 后端 | Spring Boot 2.7.18 + Java 17 + MyBatis-Plus + Redis/Redisson + MySQL 8（8081） |
| Python AI 服务 | FastAPI + openai SDK + LangGraph + ChromaDB + httpx（8000） |
| 大模型 | DeepSeek（deepseek-chat，对话/生成/function calling） |
| Embedding | 硅基流动 SiliconFlow `BAAI/bge-m3`（API + 本地 sentence-transformers 同模型兜底） |

## 三、系统架构

```
浏览器 localhost:8080
 │
 ├─ 静态页 /                          → nginx html/hmdp（黑马前端）
 ├─ /api/*（业务：店铺/笔记/券/用户）   → nginx 去 /api 前缀 → Java 8081
 ├─ /api/ai/chat|health|admin        → nginx 直连 Python 8000（SSE 流式，proxy_buffering off）
 └─ /api/ai/assist/*（笔记AI辅助）    → Java 8081 → Hutool HttpUtil 转发 → Python 8000（非流式）
                                        │
        Python 8000 ──httpx──► Java 8081（拉数据建知识库 + Agent 工具实时查询）
              │
              ├─ DeepSeek API（对话/生成/tool calling）
              ├─ SiliconFlow API（bge-m3 embedding / rerank）
              └─ ChromaDB 本地持久化（chang_an_ai/app/data/chroma）
```

**三个核心架构决策**：

1. **流式走 nginx 直连、非流式走 Java 转发**：RestTemplate 基于 HttpURLConnection 会整体缓冲响应体，SSE 会被吞成"等 20 秒一次性吐全文"；nginx 是高性能流式代理，`proxy_buffering off` 一行逐帧透传。nginx 前缀最长匹配天然分流（`/api/ai/chat` 命中更长前缀直连 8000，其余 `/api` 去 Java），前端同源 8080，无 CORS。
2. **Python 不直连 MySQL**：所有数据经 Java REST 获取，规避内网 MySQL 连通性风险，架构上 Python 是"Java 之上的智能层"。
3. **统一返回体**：Python 接口返回 `{success, errorMsg, data, total}` 信封（与 Java `Result` 一致），Java 转发零改造透传，前端拦截器（只认 success 字段）全程不破坏。

## 四、项目结构

```
chang_an_travel\
├── 项目说明.md                    # 本文档
├── chang_an_ai\                  # Python AI 服务（FastAPI）
│   ├── requirements.txt / .env / .env.example / README.md / run.py
│   ├── app\
│   │   ├── main.py               # FastAPI 入口、lifespan 懒加载向量库、全局异常
│   │   ├── config.py             # pydantic-settings 配置（API key/模型/超时/路径）
│   │   ├── routers\              # chat.py（SSE 聊天）/ assistant.py / admin.py / health.py
│   │   ├── services\             # llm / embedding / rag_service / agent_service / assistant_service / session_store
│   │   ├── repositories\         # vector_store.py（ChromaDB 封装）/ java_client.py（httpx 调 Java）
│   │   ├── agent\                # state.py / graph.py（LangGraph 状态图）/ tools.py（6 工具）
│   │   ├── prompts\              # 全部提示词（rag/rewrite/agent/title/polish/sentiment/gen_xian_data）
│   │   └── data\                 # corpus\（西安文旅语料）/ chroma\（向量库）/ sql\（西安种子数据）
│   ├── scripts\                  # ingest.py（建库）/ gen_xian_sql.py（生成西安数据）/ eval_retrieval.py（评估）/ demo_check.sh（探活）
│   └── tests\                    # pytest：fake LLM/embedding + respx mock Java
└── chang_an_backend\
    ├── chang_an_dianping\        # Java 后端（Spring Boot，8081）
    │   └── src\main\java\com\hmdp\
    │       ├── controller\       # Shop/Blog/User/Voucher/... + AiController（AI 转发）
    │       ├── service\          # 业务逻辑（缓存穿透/击穿重建/秒杀 Lua 脚本/签到 bitmap）
    │       ├── utils\            # RedisToken 登录、拦截器、分布式锁、全局 ID
    │       └── resources\db\hmdp.sql   # 原版数据 + changan_data.sql（西安版）
    └── nginx-1.18.0\             # nginx + 黑马前端静态页（8080）
```

## 五、接口设计

### 5.1 Python AI 服务（FastAPI，8000，全部挂 `/api/ai` 前缀）

| 方法 | 路径 | 入参 | 出参 | 流式 | 说明 |
|---|---|---|---|---|---|
| GET | /api/ai/health | - | `{status, kb_count, models}` | 否 | 探活 |
| POST | /api/ai/chat | `{session_id, message, mode: rag\|agent\|auto}` | SSE：`sources` → `delta`* → `tool_call`* → `done` | **是** | 聊天统一入口，auto=意图路由 |
| POST | /api/ai/assist/title | `{content, shopName?, keywords?}` | Result `{titles[5]}` | 否 | AI 标题生成（5 候选） |
| POST | /api/ai/assist/polish | `{content, style?: 文艺\|幽默\|朴实}` | Result `{polished}` | 否 | AI 润色 |
| POST | /api/ai/assist/sentiment | `{content}` 或 `{comments[]}` | Result `{sentiment, score, keywords, summary}` | 否 | 情感分析 |
| POST | /api/ai/admin/ingest | `{source?: corpus\|java\|all}` | Result `{added, total}` | 否 | 重建知识库（仅 127.0.0.1） |
| GET | /api/ai/admin/status | - | Result `{collection, count}` | 否 | 向量库状态（仅 127.0.0.1） |

### 5.2 Java 转发接口（新增 AiController）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /ai/assist/title | 转发 Python，需登录（`/ai/**` 不在免登录白名单），Hutool HttpUtil，30s 超时，Python 挂掉返回 `Result.fail("AI 服务暂不可用")` |
| POST | /ai/assist/polish | 同上 |
| POST | /ai/assist/sentiment | 同上 |

### 5.3 Python 调 Java 的接口（建库 + Agent 工具，全部免登录）

| 用途 | Java 接口 |
|---|---|
| 建库·拉全量店铺 | `GET /shop/of/type?typeId=&current=&x=&y=`（分页循环） |
| 建库·拉分类 | `GET /shop-type/list` |
| 建库·拉笔记 | `GET /blog/hot?current=`（分页；`/blog/{id}` 需登录故不用） |
| 建库·拉券 | `GET /voucher/list/{shopId}` |
| Agent·按名查店 | `GET /shop/of/name?name=` |
| Agent·按类查店 | `GET /shop/of/type?typeId=&current=1&x=&y=`（传坐标距离排序） |
| Agent·店铺详情 | `GET /shop/{id}` |
| Agent·查券（实时） | `GET /voucher/list/{shopId}` |
| Agent·查笔记 | 本地向量库（source=blog 过滤） |
| Agent·查知识 | 本地向量库（source=corpus 过滤） |

### 5.4 鉴权设计

- 前端 token 存 sessionStorage，请求头 `authorization`（Java 侧 Redis token 校验，拦截器 + ThreadLocal）。
- `/ai/assist/*` 走 Java 转发，天然要求登录（与"发笔记需登录"语义一致）。
- `/api/ai/chat` 走 nginx 直连 Python，Python 对内不设防，靠网络边界（nginx 只暴露 chat/health；admin 仅 127.0.0.1）。

## 六、核心功能设计

### 6.1 RAG 智能问答（多源知识库）

- **入库链路**：语料 chunking（md 按二级标题 400-600 字 overlap 80；店铺/笔记/券**一条记录一个 chunk**）→ bge-m3 embedding → ChromaDB（collection `changan_kb`，cosine）。
- **查询链路**：多轮 query 改写（LLM + 启发式短路）→ embedding → 召回 top8 → 重排 top4（规则基线：jieba 关键词/类型商圈匹配/质量分/MMR；可开关 bge-reranker 增强）→ 拼 prompt → DeepSeek 流式生成。
- **防幻觉**：prompt 强制"知识库没有就明说" + cosine < 0.35 阈值兜底不进 LLM + 句末 `[1][2]` 引用标注。
- **引用来源**：SSE 先发 `sources` 事件（含 shop_id），前端渲染"参考来源"卡片，点击跳转店铺/笔记详情页。

### 6.2 探店 Agent（LangGraph）

- 状态图：`START → agent(LLM+tools) → 条件边 → tools(ToolNode) ⇄ agent → END`，max_iter=6 防死循环，`astream_events v2` 流式。
- 6 个工具：查店名/查分类（带坐标）/查详情/查券（实时 HTTP）/查笔记/查知识（向量库）。
- 工具失败兜底：返回结构化错误文本给 LLM 换策略，不抛栈。
- 选型：DeepSeek 原生 function calling（ReAct 是思想、function calling 是工程实现；system prompt 保留"先分析、再调用、失败换工具"思维链要求）。

### 6.3 笔记 AI 辅助

- 标题生成（5 候选前端点选回填）、风格润色（文艺/幽默/朴实）、情感分析（发帖自检 + 评论区舆情）。
- 非流式 → 走 Java 转发，鉴权在 Java 层。

## 七、开发里程碑（6 周业余时间）

| 阶段 | 时间 | 内容 | 交付物 | 面试考点 |
|---|---|---|---|---|
| 0 骨架 | 第 1 周 | conda 3.11 环境、分层目录、config、health | curl health 通 | FastAPI/ASGI vs Flask、uvicorn 进程模型 |
| 1 数据管道 | 第 1-2 周 | java_client 拉数、西安语料 20-30 篇、embedding、ingest | Chroma 千级 chunk | embedding 选型、chunk 策略、余弦 vs 欧氏、Chroma vs Milvus |
| 2 RAG 问答 | 第 2-3 周 | 检索→重排→prompt→流式生成、query rewrite、多轮 | 脚本可流式问答带引用 | RAG 两条链路、防幻觉、SSE vs WebSocket、多轮改写 |
| 3 前端聊天页 | 第 3 周 | ai-chat.html（fetch SSE 解析）、footer 改造、nginx 分流 | **浏览器可流式演示** | nginx 前缀分流、RestTemplate 缓冲问题、ReadableStream |
| 4 笔记 AI 辅助 | 第 4 周 | Python 三接口、Java AiController、blog-edit 按钮 | 发笔记可用 AI | HTTP vs RPC/MQ、统一返回体、鉴权分层 |
| 5 探店 Agent | 第 4-5 周 | LangGraph 状态图、6 工具、意图路由 | 多轮 Agent 演示 | ReAct vs function calling、状态图设计、工具兜底、时效性分层 |
| 6 长安数据落地 | 第 5 周 | DeepSeek 生成西安 SQL、坐标校验、全链路验证 | 全站西安化 | AI 生成数据的三层质量保障 |
| 7 测试文档 | 第 5-6 周 | pytest 全套、Hit@5 评估、README | 命中率报告 | 测试如何不花钱（fake LLM）、检索评估指标 |

## 八、面试知识点汇总（按专题背诵）

### 8.1 RAG 专题
- **两条链路**：入库（chunk→embedding→向量库）与查询（改写→embedding→召回 top8→重排 top4→拼 prompt→流式生成）。
- **chunk 策略**：结构化记录是原子语义单元，一条一 chunk；md 按标题切保证语义完整；切太碎丢上下文、切太大稀释向量。
- **余弦 vs 欧氏**：语义检索用 cosine（方向而非长度），Chroma 建 collection 指定 `hnsw:space=cosine`；店铺"距离排序"是地理欧氏距离，两个场景别混。
- **防幻觉三板斧**：prompt 强制"没有就明说"、相似度阈值兜底不进 LLM、引用标注可溯源。
- **多轮指代消解**：query rewrite——"它几点关门"的"它"直接进向量库必空，用 LLM 改写 + 启发式短路（无代词跳过省一次调用）。
- **embedding 选型**：DeepSeek 无 embedding 接口；选 bge-m3 因为开源权重可本地兜底（同模型向量空间一致，断网不掉链子）、OpenAI 兼容协议、免费额度；入库与查询必须同一模型（RAG 铁律）。
- **Chroma vs Milvus**：Chroma 嵌入式单机零运维，千级数据够用；repository 抽象，百万级可平滑换 Milvus。

### 8.2 Agent 专题
- **ReAct**：Reasoning + Acting 循环（思考→选工具→执行→观察→再思考）；工程实现选 function calling（结构化 tool_calls 无需正则解析），system prompt 保留 ReAct 思维链要求。
- **LangGraph 状态图**：两节点（agent/tools）一条件边；messages 用 add_messages reducer 累加；max_iter 防死循环；astream_events v2 流式。
- **工具失败兜底**：工具内捕获异常返回结构化错误文本给 LLM 换策略，不把栈抛给用户。
- **时效性分层**：券库存实时变化走 HTTP 实时查；笔记静态走入库快照；顺带绕开 /blog/{id} 需登录的问题。

### 8.3 架构与工程专题
- **nginx 分流**：前缀最长匹配——/api/ai/chat 命中更长前缀直连 8000，其余 /api 去 Java；同源无 CORS。
- **为什么 SSE 不经过 Java**：RestTemplate 基于 HttpURLConnection 整体缓冲响应体，流式会被吞；nginx `proxy_buffering off` 逐帧透传；WebClient 要引 webflux 换线程模型，更重。
- **SSE vs WebSocket**：单向服务端推送够用就 SSE（基于 HTTP、可被 nginx 代理、自动重连）；全双工才 WebSocket。
- **Java/Python 集成为什么用 HTTP**：异构语言同步低延迟场景最低成本；gRPC 要 protobuf、MQ 是异步削峰场景；封装在 repository 层可替换。
- **统一返回体**：前端拦截器只认 success/errorMsg/data，Python 模仿 Java Result 信封让转发层零改造，一条链路三种语言格式统一。
- **鉴权分层**：Java 转发层做鉴权（/ai/** 不在白名单天然要 token），Python 对内不设防靠网络边界（nginx 只暴露 chat，admin 仅 127.0.0.1）。
- **FastAPI vs Flask**：ASGI 原生支持流式（SSE 刚需）+ pydantic 校验 + 自动 OpenAPI；Flask 是 WSGI 同步模型，流式要绕。

### 8.4 数据工程专题
- **AI 生成种子数据三层质量保障**：prompt 约束字段规则（typeId∈1..10、score×10、外键一致）→ 脚本校验（外键存在、坐标落在西安范围 108.6-109.3/34.0-34.6）→ 人工抽查。
- **坐标真实可信**：真实商圈基准经纬度 + ±0.002 随机偏移（约 200 米），保证"距离排序"演示真实可用。

### 8.5 测试与评估专题
- **不花钱跑测试**：FakeLLM（monkeypatch 预置响应）+ FakeEmbedding（确定性 hash 向量）+ respx mock httpx。
- **检索评估指标**：Hit@5（正确答案出现在 top5 的比例，目标 ≥85%）、MRR；改写前后 Hit@5 对比证明 rewrite 价值。

## 九、演示剧本（面试现场按此走）

1. 「西安三日游怎么安排？」—— RAG 纯知识回答 + 引用来源卡片。
2. 「回民街有什么好吃的店？」—— Agent：查店 → 查优惠券。
3. 「人均 50 以下再近一点」—— 多轮改写 + 距离排序工具。
4. 发笔记：AI 生成标题（5 选 1）→ AI 润色 → 情感自检。
5. `GET /api/ai/admin/status` —— 展示向量库规模与来源分布。

## 十、启动方式

```bash
# 1. MySQL + Redis 就绪后启动 Java（8081）
# 2. Python（8000）—— 虚拟环境在项目内 D 盘（conda 默认装到 C 盘用户目录，
#    且 D:\Python\Conda 需要管理员权限，故用 -p 显式建到项目内）
conda create -p D:\zmz\project\chang_an_travel\changan_ai\.venv python=3.11 -y
conda activate D:\zmz\project\chang_an_travel\changan_ai\.venv
pip install -r changan_ai/requirements.txt
cp changan_ai/.env.example changan_ai/.env   # 填 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY
python changan_ai/scripts/ingest.py          # 建知识库
python changan_ai/run.py                     # 启动 FastAPI
# 3. nginx（8080）：双击 nginx.exe（已配好 /api/ai 分流）
# 4. 浏览器打开 http://localhost:8080 → 底部「AI 助手」tab
```
