"""聊天接口：POST /api/ai/chat —— SSE 流式（nginx 直连分流）。

mode 三种取值：
- rag：RAG 直答（阶段 2 链路）；
- agent：LangGraph Agent（阶段 5，工具调用 + 流式）；
- auto（默认）：启发式意图路由——含任务型关键词（找店/券/人均等）走 Agent，
  否则走 RAG。这是零成本基线；LLM 意图分类是可选升级（多一次调用换准确率）。

SSE 事件协议：
  event: status     data: "正在检索知识库…"  进度提示（新增，消除"卡死"感）
  event: sources    data: [...]              引用来源（RAG 模式先发）
  event: tool_call  data: {name, input}      工具调用开始（Agent 模式，前端显示状态）
  event: delta      data: "文本"             生成增量（打字机）
  event: done       data: {}                 结束
  event: error      data: {message}          异常（也走 SSE，不裸断流）

技术点：
- RAG 路径是同步生成器（DeepSeek SDK 同步流），用 starlette 的
  iterate_in_threadpool 桥接到 async 端点，不阻塞事件循环；
- Agent 路径用 astream_events v2：on_chat_model_stream 拿打字机增量、
  on_tool_start 拿工具事件——一个流式 API 同时覆盖两件事。
"""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool

from app.agent.graph import get_agent
from app.services.rag_service import answer
from app.services.session_store import append, get_history

router = APIRouter(prefix="/api/ai", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str
    message: str
    mode: str = "auto"  # rag | agent | auto


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 意图路由关键词：出现即认为用户想做"任务型查询"（找店/查券），走 Agent
_AGENT_KEYWORDS = ("帮我找", "附近", "人均", "优惠券", "券", "有什么店", "哪家店", "店铺", "探店", "推荐")


def _route(req: ChatRequest) -> str:
    if req.mode == "agent":
        return "agent"
    if req.mode == "rag":
        return "rag"
    return "agent" if any(k in req.message for k in _AGENT_KEYWORDS) else "rag"


def _rag_frames(req: ChatRequest, history: list[dict]):
    """RAG 模式（同步生成器）：沿用阶段 2 的 answer() 编排，转为 SSE 帧。"""
    collected: list[str] = []
    try:
        for event, payload in answer(req.message, history):
            if event == "status":
                yield _sse("status", payload)
            elif event == "sources":
                yield _sse("sources", payload)
            elif event == "delta":
                collected.append(payload)
                yield _sse("delta", payload)
            else:
                yield _sse("done", {})
    except Exception as e:
        yield _sse("error", {"message": f"AI 服务出错: {e}"})
    append(req.session_id, "assistant", "".join(collected))


async def _agent_frames(req: ChatRequest, history: list[dict]):
    """Agent 模式（async）：astream_events v2 流式。

    messages 用 (role, content) 元组序列——LangGraph 会自动转成 BaseMessage。
    history 已含本轮用户消息（chat() 里先 append）。
    """
    messages = [(m["role"], m["content"]) for m in history]
    collected: list[str] = []
    try:
        async for ev in get_agent().astream_events(
            {"messages": messages}, version="v2", config={"recursion_limit": 13}
        ):
            kind = ev["event"]
            if kind == "on_chat_model_stream":
                chunk = ev["data"]["chunk"]
                if chunk.content:  # 工具调用轮的 chunk 只有 tool_calls，无内容——跳过
                    collected.append(chunk.content)
                    yield _sse("delta", chunk.content)
            elif kind == "on_tool_start":
                # input 是工具实参 dict，发给前端展示"正在查什么"
                yield _sse("tool_call", {"name": ev["name"], "input": ev["data"].get("input")})
    except Exception as e:
        yield _sse("error", {"message": f"Agent 出错: {e}"})
    if not collected:
        yield _sse("delta", "抱歉，暂时没能查到结果，可以换个说法再试试～")
    yield _sse("done", {})
    append(req.session_id, "assistant", "".join(collected))


@router.post("/chat")
async def chat(req: ChatRequest):
    append(req.session_id, "user", req.message)
    history = get_history(req.session_id)
    if _route(req) == "agent":
        gen = _agent_frames(req, history)
    else:
        gen = iterate_in_threadpool(_rag_frames(req, history))
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 提示 nginx 不要缓冲本响应
        },
    )