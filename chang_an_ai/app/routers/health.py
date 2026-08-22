"""探活接口：GET /api/ai/health（nginx 直连分流，无鉴权）。"""
from fastapi import APIRouter

from app.config import settings
from app.repositories.vector_store import get_store

router = APIRouter(prefix="/api/ai", tags=["health"])


@router.get("/health")
def health() -> dict:
    # 探活接口的原则：自身永远不能挂——向量库异常时返回 kb_count=-1 标记，而不是抛 500
    try:
        kb_count = get_store().count()
    except Exception:
        kb_count = -1
    return {
        "status": "ok",
        "kb_count": kb_count,
        "models": {
            "llm": settings.deepseek_model,
            "embedding": settings.embedding_model,
        },
    }
