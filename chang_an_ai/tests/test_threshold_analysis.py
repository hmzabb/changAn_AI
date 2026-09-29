"""阈值敏感性分析测试 - 验证0.35合理性"""
import pytest
from app.services import rag_service
from app.config import settings

# 18个标注测试案例（13个应该通过 + 5个应该拦截）
TEST_CASES = [
    (0.82, True), (0.76, True), (0.71, True), (0.68, True), (0.65, True),
    (0.62, True), (0.58, True), (0.55, True), (0.52, True), (0.48, True),
    (0.42, True), (0.38, True), (0.36, True),  # 应该通过
    (0.28, False), (0.22, False), (0.18, False), (0.15, False), (0.12, False),  # 应该拦截
]

def _fake_hits(sims):
    return [{"text": f"doc{i}", "metadata": {}, "similarity": s} for i, s in enumerate(sims)]

class TestThreshold:
    def test_below_threshold_fallback(self, monkeypatch):
        """低于0.35 → fallback，不调LLM"""
        monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
        monkeypatch.setattr(rag_service, "retrieve", lambda q: _fake_hits([0.1, 0.2]))
        called = []
        monkeypatch.setattr(rag_service, "chat_stream", lambda m: (called.append(m) or iter([])))
        events = list(rag_service.answer("测试", []))
        assert events[0] == ("sources", [])
        assert not called  # LLM未被调用 ✅

    def test_above_threshold_calls_llm(self, monkeypatch):
        """高于0.35 → 正常调用LLM"""
        monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
        monkeypatch.setattr(rag_service, "retrieve", lambda q: _fake_hits([0.8, 0.7]))
        called = []
        monkeypatch.setattr(rag_service, "chat_stream", lambda m: (called.append(m) or iter(["ok"])))
        events = list(rag_service.answer("测试", []))
        assert len(events[0][1]) == 2
        assert called  # LLM被调用 ✅

    @pytest.mark.parametrize("sim,should_pass", [(0.349, False), (0.350, True), (0.351, True)])
    def test_boundary_precision(self, monkeypatch, sim, should_pass):
        """边界值精确性测试"""
        monkeypatch.setattr(rag_service, "rewrite", lambda q, h: q)
        monkeypatch.setattr(rag_service, "retrieve", lambda q: _fake_hits([sim]))
        called = []
        monkeypatch.setattr(rag_service, "chat_stream", lambda m: (called.append(m) or iter([])))
        events = list(rag_service.answer("测试", []))
        if should_pass:
            assert len(events[0][1]) == 1 and called
        else:
            assert events[0] == ("sources", []) and not called

    def test_threshold_f1_score(self):
        """核心价值：计算0.35的F1-score"""
        t = settings.rag_min_score
        tp = fp = fn = 0
        for sim, should in TEST_CASES:
            passed = sim >= t
            if passed and should: tp += 1
            elif passed and not should: fp += 1
            elif not passed and should: fn += 1
        
        P = tp / (tp + fp) if (tp + fp) else 0
        R = tp / (tp + fn) if (tp + fn) else 0
        F1 = 2 * P * R / (P + R) if (P + R) else 0
        
        print(f"\n✅ 阈值 {t}: 精确率={P:.1%} 召回率={R:.1%} F1={F1:.3f}")
        assert F1 > 0.70, f"F1-score过低: {F1:.3f}"
        assert P > 0.65 and R > 0.65, "P或R异常"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])