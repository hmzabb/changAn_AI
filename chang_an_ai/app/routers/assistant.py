"""笔记 AI 辅助接口（非流式 → 走 Java 转发，鉴权在 Java 层）。

统一返回体（面试必考）：三个接口全部返回 Result 信封
{success, errorMsg, data, total}——与 Java Result 完全一致，
Java AiController 原样透传、前端拦截器只认 success 字段，三层零适配。
LLM 异常返回 success=false 信封而非 500：对用户是"服务暂不可用"，
对前端是统一处理路径。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.services import assistant_service

router = APIRouter(prefix="/api/ai/assist", tags=["assistant"])


def _ok(data) -> dict:
    return {"success": True, "errorMsg": None, "data": data, "total": None}


def _fail(msg: str) -> dict:
    return {"success": False, "errorMsg": msg, "data": None, "total": None}


class TitleRequest(BaseModel):
    content: str
    shopName: str | None = None
    keywords: str | None = None


class PolishRequest(BaseModel):
    content: str
    style: str = "文艺"  # 文艺 | 幽默 | 朴实


class SentimentRequest(BaseModel):
    content: str | None = None
    comments: list[str] | None = None  # 评论区舆情场景：传评论列表


@router.post("/title")
def title(req: TitleRequest):
    if not req.content.strip():
        return _fail("笔记内容不能为空")
    try:
        titles = assistant_service.generate_titles(
            req.content, shop_name=req.shopName or "", keywords=req.keywords or ""
        )
        return _ok({"titles": titles})
    except Exception as e:
        return _fail(f"AI 标题生成失败: {e}")


@router.post("/polish")
def polish(req: PolishRequest):
    if not req.content.strip():
        return _fail("笔记内容不能为空")
    try:
        return _ok({"polished": assistant_service.polish(req.content, req.style)})
    except Exception as e:
        return _fail(f"AI 润色失败: {e}")


@router.post("/sentiment")
def sentiment(req: SentimentRequest):
    # 两种入参：发帖自检传 content，评论区舆情传 comments 列表
    text = req.content or ""
    if req.comments:
        # 评论拼成编号列表，限制 20 条控 token
        text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(req.comments[:20]))
    if not text.strip():
        return _fail("内容不能为空")
    try:
        return _ok(assistant_service.analyze_sentiment(text))
    except Exception as e:
        return _fail(f"AI 情感分析失败: {e}")
