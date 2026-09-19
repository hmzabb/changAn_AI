"""Agent 工具集（6 个）：4 个实时查 Java + 2 个查本地向量库。

- 工具失败兜底：每个工具内部 try/except，返回结构化错误文本给 LLM 换策略，
  绝不把栈抛给用户（LLM 看到错误会自己换工具/换参数）；
- 时效性分层：券库存实时变化 → HTTP 实时查；笔记/知识静态 → 向量库快照查；
- 全部命中 Java 免登录接口（聊天走 nginx 直连绕开了 Java 鉴权，
  工具若用需登录接口就得层层传 token，复杂度爆炸——选型时刻意规避）；
- 返回 JSON 字符串：ToolNode 会把它包成 ToolMessage 喂回 LLM，
  JSON 结构化数据比自然语言更利于模型提取字段。
"""
from __future__ import annotations

import json

from langchain_core.tools import tool

from app.repositories.java_client import JavaClient, JavaClientError
from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# ==================== Java 实时查询（4 个） ====================

@tool
def search_shops_by_name(name: str) -> str:
    """按名称关键字搜索平台店铺（模糊匹配）。参数 name：店铺名称关键字，如"泡馍"。"""
    try:
        with JavaClient() as jc:
            shops = jc.list_shops_by_name(name)
        return _json({"count": len(shops), "shops": shops})
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_shops_by_type(type_name: str, x: float | None = None, y: float | None = None) -> str:
    """按分类查店铺列表；传坐标 (x,y) 时按距离从近到远排序。
    参数 type_name：分类名（美食/KTV/酒吧/轰趴馆/美容SPA/健身运动/按摩·足疗/丽人·美发/亲子游乐/美睫·美甲）；
    参数 x/y：用户位置的经度/纬度（可选，如"钟楼"约 x=108.9425, y=34.2610）。"""
    try:
        with JavaClient() as jc:
            types = jc.list_shop_types()
            type_id = next((t["id"] for t in types if t["name"] == type_name), None)
            if type_id is None:
                return _json({"error": f"没有找到分类「{type_name}」，可用分类: {[t['name'] for t in types]}"})
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
    """查询店铺详情（评分/人均/营业时间/地址等）。参数 shop_id：店铺 id。"""
    try:
        with JavaClient() as jc:
            shop = jc.get_shop_detail(shop_id)
        return _json(shop)
    except JavaClientError as e:
        return _json({"error": f"查询失败: {e}"})


@tool
def list_vouchers(shop_id: int) -> str:
    """实时查询店铺的优惠券（含库存/有效期）。参数 shop_id：店铺 id。"""
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
    """搜索平台上的探店笔记（本地向量库快照）。参数 query：检索词，如"回民街泡馍体验"。"""
    return _search_kb(query, where={"source": "java", "type": "blog"})


@tool
def search_knowledge(query: str) -> str:
    """搜索西安文旅知识（景点/美食/攻略等语料库）。参数 query：检索词，如"大雁塔门票"。"""
    return _search_kb(query, where={"source": "corpus"})


ALL_TOOLS = [
    search_shops_by_name,
    list_shops_by_type,
    get_shop_detail,
    list_vouchers,
    search_blogs,
    search_knowledge,
]
