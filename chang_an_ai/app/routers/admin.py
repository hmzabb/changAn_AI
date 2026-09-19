"""管理接口：建库与状态查询（仅限本机访问）。

都属于运维面操作，这里用 request.client.host 限制 127.0.0.1；
将来上 nginx 时还有第二道 allow 127.0.0.1 兜底（见 项目说明.md 5.4）。

性能注意：ingest 是同步函数，FastAPI 会放到线程池执行（不阻塞事件循环），
个人项目够用；生产环境应换成 BackgroundTasks 或消息队列异步建库。
"""
from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from app.config import settings
from app.repositories.vector_store import COLLECTION_NAME, get_store
from app.services.ingest_service import run_ingest

router = APIRouter(prefix="/api/ai/admin", tags=["admin"])

_ALLOWED_HOSTS = ("127.0.0.1", "::1", "localhost")


def _require_localhost(request: Request) -> None:
    if request.client is None or request.client.host not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="管理接口仅限本机访问")


class IngestRequest(BaseModel):
    source: str | None = None  # corpus | java | all；空 = 全部重建


@router.post("/ingest")
def ingest(req: IngestRequest, request: Request):
    _require_localhost(request)
    sources = None if req.source in (None, "all") else [req.source]
    log: list[str] = []
    report = run_ingest(sources, progress=log.append)
    # 统一 Result 信封（与 Java Result 一致，见 项目说明.md 5.4）
    return {"success": True, "errorMsg": None, "data": {"report": report, "log": log}, "total": None}


@router.get("/status")
def status(request: Request):
    _require_localhost(request)
    store = get_store()
    return {
        "success": True,
        "errorMsg": None,
        "data": {
            "collection": COLLECTION_NAME,
            **store.source_stats(),
            "embedding": settings.embedding_model,
        },
        "total": None,
    }
