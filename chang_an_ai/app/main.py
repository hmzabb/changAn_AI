"""FastAPI 应用入口：生命周期管理、路由挂载、全局异常处理。

面试考点：
- ASGI 应用：原生支持 SSE 流式响应（本项目聊天接口的刚需），Flask(WSGI) 做不到这么直接。
- lifespan：启动钩子，向量库懒加载挂在这里（阶段 1 实现），不阻塞启动；Milvus 是独立服务，与离线 ingest 脚本并行也不冲突。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import admin, assistant, chat, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 阶段 1：向量库按需懒加载（Milvus 是独立服务，无需在启动时建库）
    yield


app = FastAPI(title="长安文旅探店助手 AI 服务", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(assistant.router)
app.include_router(admin.router)


# 注意：fastapi 0.141 起 HTTPException 拆成两类——fastapi.exceptions.HTTPException（子类）
# 与 starlette.exceptions.HTTPException（基类，starlette 路由抛 404 时用的是基类实例）。
# 注册在基类上，MRO 查找可同时覆盖两者。
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "errorMsg": exc.detail, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(
        status_code=422,
        content={"success": False, "errorMsg": f"参数校验失败: {first}", "data": None},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # 统一 Result 信封（success/errorMsg/data），不让裸 500 破坏前端拦截器约定
    return JSONResponse(
        status_code=500,
        content={"success": False, "errorMsg": f"AI 服务内部错误: {exc}", "data": None},
    )
