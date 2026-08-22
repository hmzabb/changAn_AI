"""rag_service 测试：mock 检索与 LLM（不花 API 钱），验证防幻觉与引用链路。"""
from app.services import rag_service


def _fake_hits(similarities):
    return [
        {"text": f"片段{i}", "metadata": {"source": "corpus", "doc": f"文档{i}"}, "similarity": s}
        for i, s in enumerate(similarities)
    ]


def test_threshold_fallback_no_llm(monkeypatch):
    """全部低于阈值 → 直接兜底话术，不进 LLM（防幻觉机制之二）。"""
    monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
    monkeypatch.setattr(rag_service, "retrieve", lambda q: _fake_hits([0.1, 0.2]))
    called = []
    monkeypatch.setattr(rag_service, "chat_stream", lambda m: called.append(m) or iter([]))
    events = list(rag_service.answer("北京烤鸭哪家好吃", []))
    assert events[0] == ("sources", [])
    assert "知识库" in events[1][1]
    assert not called  # LLM 未被调用


def test_answer_builds_citation_prompt(monkeypatch):
    """有有效命中 → sources 事件 + prompt 带 [1][2] 编号（防幻觉机制之一/三）。"""
    monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
    monkeypatch.setattr(rag_service, "retrieve", lambda q: _fake_hits([0.8, 0.7]))
    captured = {}
    monkeypatch.setattr(rag_service, "chat_stream", lambda m: captured.update(messages=m) or iter(["好", "的"]))
    events = list(rag_service.answer("三日游", []))
    assert events[0][0] == "sources"
    assert len(events[0][1]) == 2 and events[0][1][0]["index"] == 1
    assert "[1] 片段0" in captured["messages"][1]["content"]  # 上下文带编号
    assert [e for e in events if e[0] == "delta"] == [("delta", "好"), ("delta", "的")]
    assert events[-1] == ("done", None)


def test_sources_shop_id_for_jump(monkeypatch):
    """店铺来源的 sources 事件要带 shop_id（前端来源卡片跳详情页）。"""
    monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
    monkeypatch.setattr(rag_service, "retrieve", lambda q: [{
        "text": "店铺文本", "metadata": {"source": "java", "type": "shop", "id": 7, "name": "老孙家"},
        "similarity": 0.7,
    }])
    monkeypatch.setattr(rag_service, "chat_stream", lambda m: iter(["ok"]))
    events = list(rag_service.answer("查询", []))
    assert events[0][1][0]["shop_id"] == 7
    assert events[0][1][0]["title"] == "老孙家"
