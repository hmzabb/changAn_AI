"""assistant_service 测试：mock chat_sync（LLM 输出解析的容错是重点）。"""
from app.services import assistant_service


def test_generate_titles_parses_lines(monkeypatch):
    monkeypatch.setattr(assistant_service, "chat_sync",
                        lambda m, **kw: "标题一\n标题二\n- 标题三\n\n标题四\n标题五")
    titles = assistant_service.generate_titles("内容")
    assert titles == ["标题一", "标题二", "标题三", "标题四", "标题五"]


def test_generate_titles_filters_noise(monkeypatch):
    # 空行/超长（>40 字）都要被清洗
    monkeypatch.setattr(assistant_service, "chat_sync",
                        lambda m, **kw: "1. 正常标题\n\n" + "超长" * 21 + "\n\n\n")
    titles = assistant_service.generate_titles("内容")
    assert any("正常标题" in t for t in titles) and not any("超长" in t for t in titles)


def test_polish_style_whitelist(monkeypatch):
    captured = {}
    monkeypatch.setattr(assistant_service, "chat_sync",
                        lambda m, **kw: captured.update(system=m[0]["content"]) or "润色后")
    out = assistant_service.polish("原文", style="不存在的风格")
    assert "文艺" in captured["system"]  # 非法风格回退文艺
    assert out == "润色后"


def test_polish_fallback_on_llm_error(monkeypatch):
    def boom(m, **kw):
        raise RuntimeError("LLM 挂了")
    monkeypatch.setattr(assistant_service, "chat_sync", boom)
    assert assistant_service.polish("原文") == "原文"  # 回退原文，不阻塞发笔记


def test_sentiment_parses_fenced_json(monkeypatch):
    # 模型常输出 ```json 围栏 —— 正则提取要兜住
    monkeypatch.setattr(assistant_service, "chat_sync",
                        lambda m, **kw: '```json\n{"sentiment": "负面", "score": 20, '
                                        '"keywords": ["慢", "贵"], "summary": "体验差"}\n```')
    r = assistant_service.analyze_sentiment("排队太久")
    assert r["sentiment"] == "负面" and r["score"] == 20
    assert r["keywords"] == ["慢", "贵"]


def test_sentiment_fallback_on_bad_json(monkeypatch):
    monkeypatch.setattr(assistant_service, "chat_sync", lambda m, **kw: "这不是JSON")
    r = assistant_service.analyze_sentiment("任意")
    assert r["sentiment"] == "中性" and r["score"] == 50
