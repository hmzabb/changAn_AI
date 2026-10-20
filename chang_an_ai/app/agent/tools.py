"""Agent 工具集（6 个）：4 个实时查 Java + 2 个查本地向量库。

功能说明：
- 工具失败兜底：每个工具内部 try/except，返回结构化错误文本给 LLM 换策略，
  绝不把栈抛给用户（LLM 看到错误会自己换工具/换参数）；
- 时效性分层：券库存实时变化 → HTTP 实时查；笔记/知识静态 → 向量库快照查；
- 全部命中 Java 免登录接口（聊天走 nginx 直连绕开了 Java 鉴权，
  工具若用需登录接口就得层层传 token，复杂度爆炸——选型时刻意规避）；
- 返回 JSON 字符串：ToolNode 会把它包成 ToolMessage 喂回 LLM，
  JSON 结构化数据比自然语言更利于模型提取字段。

防御体系（V3.0 - 防止LLM乱调工具）：
- 第一层：优化工具描述，明确使用场景和反例
- 第二层：参数校验守卫（tool_guard.py），拦截非法参数
- 第三层：调用审计日志（graph.py），检测异常调用模式
- 第四层：结果质量检查（parallel_tools.py），修正空结果/异常结果
- 第五层：强化System Prompt（agent.py），明确决策树
- 第六层：结构化约束（可选），强制思考步骤

缓存策略：
- 缓存逻辑已抽取到 app.agent.cache 模块；
- 使用 CachedToolsWrapper 可为所有工具自动添加分级缓存能力。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from langchain_core.tools import tool

from app.repositories.java_client import JavaClient, JavaClientError
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client
from app.agent.tool_guard import ToolGuard

logger = logging.getLogger(__name__)


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# ==================== Java 实时查询（4 个） ====================

@tool
def search_shops_by_name(name: str) -> str:
    """按店铺名称关键字搜索平台店铺（模糊匹配）。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户提到具体店名（如"老孙家泡馍怎么样"、"XX火锅店"）
    - 用户想找某类具体店铺但提到了名称关键词（如"找一家泡馍"）
    - 用户问某个店铺的详细信息（先搜到店铺ID，再调get_shop_detail）

    【不要使用此工具的场景】❌ 以下情况请用其他工具：
    - 用户说"推荐几个火锅店"、"附近有什么好吃的" → 用 list_shops_by_type（这是分类查询！）
    - 用户问景点、攻略、历史知识 → 用 search_knowledge
    - 用户问体验、评价、笔记 → 用 search_blogs

    【参数说明】
    name: 店铺名称关键字（必填），如"泡馍"、"老孙家"、"海底捞"
          ⚠️ 不能为空，不能超过50个字符

    【返回示例】
    {"count": 5, "shops": [{"id": 1, "name": "老孙家泡馍", "score": 4.8, ...}]}
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_search_query(name)
    if error:
        logger.warning(f"[ToolGuard] search_shops_by_name 参数被拦截: {error}")
        return _json({"error": error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call("search_shops_by_name", {"name": name})
    if dup_error:
        return _json({"error": dup_error})

    try:
        with JavaClient() as jc:
            shops = jc.list_shops_by_name(name)
        return _json({"count": len(shops), "shops": shops})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_shops_by_type(type_name: str, x: float | None = None, y: float | None = None) -> str:
    """按分类查店铺列表；传坐标 (x,y) 时按距离从近到远排序。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户问分类推荐（如"推荐火锅店"、"KTV哪家好"、"美食推荐"）
    - 用户说"附近有什么好吃的"、"钟楼附近的餐厅"
    - 用户要求按位置查找店铺
    - 用户需要获取店铺列表以便后续查优惠券

    【不要使用此工具的场景】❌ 以下情况请用其他工具：
    - 用户提到具体店名（如"老孙家泡馍"）→ 用 search_shops_by_name
    - 用户直接给了shop_id要查详情 → 用 get_shop_detail
    - 用户问景点、攻略 → 用 search_knowledge

    【参数说明】
    type_name: 分类名（必填），可选值：
      - 美食（餐厅、小吃、快餐等）
      - KTV / 酒吧 / 轰趴馆
      - 美容SPA / 健身运动 / 按摩·足疗
      - 丽人·美发 / 亲子游乐 / 美睫·美甲
      ⚠️ 只能是以上值之一，其他值会被拒绝！

    x/y: 用户位置的经度/纬度（可选，用于距离排序）
      - 不传或都为None：返回该分类所有店铺（默认排序）
      - 传入坐标：按距离从近到远排序
      - 常见商圈坐标参考：
        * 钟楼: x=108.9425, y=34.2610
        * 回民街: x=108.9403, y=34.2622
        * 小寨: x=108.9488, y=34.2303
        * 大雁塔: x=108.9644, y=34.2186
      ⚠️ 坐标必须在西安范围内(经度108.7~109.2, 纬度34.1~34.5)

    【返回示例】
    {"count": 10, "shops": [{"id": 1, "name": "XX火锅", "type": "美食", "score": 4.7, "x": 108.94, "y": 34.26, ...}]}
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_shop_type(type_name)
    if error:
        logger.warning(f"[ToolGuard] list_shops_by_type type_name被拦截: {error}")
        return _json({"error": error})

    coord_error = ToolGuard.validate_coordinates(x, y)
    if coord_error:
        logger.warning(f"[ToolGuard] list_shops_by_type 坐标被拦截: {coord_error}")
        return _json({"error": coord_error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call(
        "list_shops_by_type",
        {"type_name": type_name, "x": x, "y": y}
    )
    if dup_error:
        return _json({"error": dup_error})

    try:
        with JavaClient() as jc:
            types = jc.list_shop_types()
            type_id = next((t["id"] for t in types if t["name"] == type_name), None)
            if type_id is None:
                return _json({
                    "error": f"没有找到分类「{type_name}」，可用分类: {[t['name'] for t in types]}",
                    "suggestion": "请使用正确的分类名"
                })
            shops: list = []
            for page in (1, 2):  # 最多取 2 页（10 家），足够回答"有哪些店"
                batch = jc.list_shops_by_type(type_id, page=page, x=x, y=y)
                shops.extend(batch)
                if len(batch) < jc.SHOP_PAGE_SIZE:
                    break
        return _json({"count": len(shops), "shops": shops})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def get_shop_detail(shop_id: int) -> str:
    """查询店铺详情（评分/人均/营业时间/地址等）。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户询问某个店铺的详细信息（评分、人均消费、营业时间、地址等）
    - 已经通过搜索获取了shop_id，需要展示完整信息
    - 用户对比多家店铺时需要详细数据

    【不要使用此工具的场景】❌ 以下情况请用其他工具：
    - 用户不知道shop_id → 先用 search_shops_by_name 或 list_shops_by_type 获取
    - 用户只是想找店铺列表 → 用 search_shops_by_name 或 list_shops_by_type
    - ⚠️ 绝对不要自己编造shop_id！

    【参数说明】
    shop_id: 店铺ID（必填，正整数）
      - 必须从 search_shops_by_name 或 list_shops_by_type 的返回结果中获取
      - 合理范围：1 ~ 99999
      - ❌ 禁止自己编造数字或猜测ID！

    【返回示例】
    {"id": 1, "name": "XX火锅", "score": 4.7, "avg_price": 88, "address": "钟楼东大街...", "hours": "10:00-22:00", ...}
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_shop_id(shop_id)
    if error:
        logger.warning(f"[ToolGuard] get_shop_detail 参数被拦截: {error}")
        return _json({"error": error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call("get_shop_detail", {"shop_id": shop_id})
    if dup_error:
        return _json({"error": dup_error})

    try:
        with JavaClient() as jc:
            shop = jc.get_shop_detail(shop_id)
        return _json(shop)
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_vouchers(shop_id: int) -> str:
    """实时查询店铺的优惠券（含库存/有效期）。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户询问某个店铺的优惠、团购、折扣信息
    - 用户说"有优惠券吗"、"有什么优惠活动"
    - 推荐店铺后主动告知优惠信息（提升用户体验）

    【调用顺序重要】⚠️：
    1. 先调 list_shops_by_type 或 search_shops_by_name 获取店铺列表
    2. 从返回结果中拿到真实的 shop_id
    3. 再调 list_vouchers(shop_id) 查询优惠券
    ❌ 不要跳过步骤1-2直接调用此工具！

    【不要使用此工具的场景】❌：
    - 还没有获取到shop_id → 先去查店铺
    - shop_id不是从前面的工具返回中获取的 → 这是错误的！

    【参数说明】
    shop_id: 店铺ID（必填，正整数）
      - 必须从前面搜索工具的返回结果中的 "id" 字段获取
      - 合理范围：1 ~ 99999
      - ❌ 绝对禁止自己编造或猜测shop_id！

    【返回示例】
    {"count": 3, "vouchers": [{"id": 1, "title": "满100减20", "price": 80, "stock": 50, "expire_date": "2024-12-31", ...}]}
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_shop_id(shop_id)
    if error:
        logger.warning(f"[ToolGuard] list_vouchers 参数被拦截: {error}")
        return _json({"error": error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call("list_vouchers", {"shop_id": shop_id})
    if dup_error:
        return _json({"error": dup_error})

    try:
        with JavaClient() as jc:
            vouchers = jc.list_vouchers(shop_id)
        return _json({"count": len(vouchers), "vouchers": vouchers})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


# ==================== 本地向量库（2 个） ====================

def _search_kb(query: str, where: dict, top_k: int = 3) -> str:
    """向量检索公共实现：embedding → Milvus 过滤检索。"""
    try:
        qv = get_embedding_client().embed_texts([query])[0]
        hits = get_store().query(qv, top_k=top_k, where=where)
        return _json([{
            "title": h["metadata"].get("title") or h["metadata"].get("doc") or h["metadata"].get("name"),
            "score": round(h["score"], 3),
            "text": h["text"][:300],
        } for h in hits])
    except Exception as e:
        return _json({"error": f"知识库检索失败: {e}"})


@tool
def search_blogs(query: str) -> str:
    """搜索平台上的探店笔记（本地向量库快照）。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户询问其他用户的体验、评价、探店笔记
    - 用户说"大家的评价怎么样"、"有人去过吗"
    - 用户想了解真实的用餐/游玩体验
    - 用户做购买决策前想看口碑

    【不要使用此工具的场景】❌ 以下情况请用其他工具：
    - 用户问店铺基本信息（评分/价格/地址）→ 用 get_shop_detail 或搜索工具
    - 用户问景点/攻略/历史知识 → 用 search_knowledge
    - 用户想找店铺 → 用 search_shops_by_name 或 list_shops_by_type

    【参数说明】
    query: 检索词（必填），如"回民街泡馍体验"、"钟楼火锅评价"
           ⚠️ 不能为空，建议包含地点+关键词

    【返回示例】
    [{"title": "回民街老孙家泡馍体验", "score": 0.92, "text": "今天去了老孙家..."}]
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_search_query(query)
    if error:
        logger.warning(f"[ToolGuard] search_blogs 参数被拦截: {error}")
        return _json({"error": error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call("search_blogs", {"query": query})
    if dup_error:
        return _json({"error": dup_error})

    return _search_kb(query, where={"source": "java", "type": "blog"})


@tool
def search_knowledge(query: str) -> str:
    """搜索西安文旅知识（景点/美食/攻略等语料库）。

    【使用场景】✅ 必须在以下情况使用此工具：
    - 用户询问西安景点信息（大雁塔、兵马俑、城墙、华清宫等）
    - 用户问门票价格、开放时间、游玩攻略
    - 用户问西安美食文化、特色小吃介绍
    - 用户问交通指南、行程规划建议
    - 用户问历史文化背景（如"大雁塔的历史"）

    【不要使用此工具的场景】❌ 以下情况请用其他工具：
    - 用户想找具体店铺/餐厅 → 用 search_shops_by_name 或 list_shops_by_type
    - 用户要看真实用户体验/评价 → 用 search_blogs
    - 用户问优惠/团购 → 用 list_vouchers

    【参数说明】
    query: 检索词（必填），如"大雁塔门票"、"兵马俑开放时间"、"西安三日游攻略"
           ⚠️ 不能为空，建议具体明确

    【返回示例】
    [{"title": "大雁塔游览攻略", "score": 0.95, "text": "大雁塔位于...门票50元..."}]
    """
    # 第二层防御：参数校验
    error = ToolGuard.validate_search_query(query)
    if error:
        logger.warning(f"[ToolGuard] search_knowledge 参数被拦截: {error}")
        return _json({"error": error})

    # 防重复调用
    dup_error = ToolGuard.check_duplicate_call("search_knowledge", {"query": query})
    if dup_error:
        return _json({"error": dup_error})

    return _search_kb(query, where={"source": "corpus"})


ALL_TOOLS = [
    search_shops_by_name,
    list_shops_by_type,
    get_shop_detail,
    list_vouchers,
    search_blogs,
    search_knowledge,
]