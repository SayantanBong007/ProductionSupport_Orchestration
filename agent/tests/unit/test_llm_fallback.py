import asyncio

import pytest

from sre_agent.core import llm


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(llm, "LLM_FALLBACK_MODELS", ["fallback-model"])
    monkeypatch.setattr(llm, "_exhausted_until", {})


def _fake_retries(exhausted: set[str], calls: list[str]):
    async def with_retries(call, *, agent, emit):
        model = await call()
        calls.append(model)
        if model in exhausted:
            raise llm.QuotaExhausted("rate limit quota exhausted (retry in 600s)", 600)
        return f"answer from {model}"
    return with_retries


def _make_call(m):
    async def call():
        return m
    return call


def test_exhausted_model_falls_back_and_is_then_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_with_retries", _fake_retries({"primary"}, calls))

    first = asyncio.run(llm._with_fallback(_make_call, model="primary", agent="T", emit=None))
    second = asyncio.run(llm._with_fallback(_make_call, model="primary", agent="T", emit=None))

    assert first == ("answer from fallback-model", "fallback-model")
    assert second == ("answer from fallback-model", "fallback-model")
    assert calls == ["primary", "fallback-model", "fallback-model"]   # primary not retried while exhausted


def test_all_models_exhausted_raises_unavailable(monkeypatch):
    monkeypatch.setattr(llm, "_with_retries", _fake_retries({"primary", "fallback-model"}, []))
    with pytest.raises(llm.LLMUnavailable, match="quota exhausted"):
        asyncio.run(llm._with_fallback(_make_call, model="primary", agent="T", emit=None))
