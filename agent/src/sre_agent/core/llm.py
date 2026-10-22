"""
Shared LLM client — any OpenAI-compatible endpoint (Groq by default).

  chat()        one non-streaming completion, with tool calling — used by the
                agent loops in agents.py
  stream_llm()  streams a plain-text answer token-by-token to the UI

Rate limits (HTTP 429) are retried using the provider's own retry-after /
x-ratelimit-reset-tokens headers. Any other failure raises LLMUnavailable:
there is no canned fallback text — callers must surface the outage honestly.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

import openai

from sre_agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_FALLBACK_MODELS, LLM_REASONING_EFFORT

logger = logging.getLogger("llm")

Emit = Callable[[str, dict], Awaitable[None]]

# Waiting longer than this for a rate limit means a daily quota is exhausted.
_MAX_RATE_LIMIT_WAIT = 150.0
_MAX_ATTEMPTS        = 8

_client: openai.AsyncOpenAI | None = None


class LLMUnavailable(RuntimeError):
    """The LLM provider could not produce an answer."""


class ContextTooLarge(LLMUnavailable):
    """The request exceeds the provider's per-request size limit (HTTP 413)."""


class QuotaExhausted(LLMUnavailable):
    """The model's quota is used up for longer than is worth waiting."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


# Models whose quota ran out, and when they become usable again (monotonic time).
# Shared by all agents, so a known-exhausted model is skipped without a wasted call.
_exhausted_until: dict[str, float] = {}


def _model_chain(model: str) -> list[str]:
    """The requested model first, then the configured fallbacks — skipping exhausted ones."""
    chain = [model] + [m for m in LLM_FALLBACK_MODELS if m != model]
    now = time.monotonic()
    usable = [m for m in chain if _exhausted_until.get(m, 0) <= now]
    return usable or chain


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            max_retries=0,          # retries are handled here, with provider-aware waits
            timeout=90.0,
        )
    return _client


def _extra_body(model: str) -> dict | None:
    # reasoning_effort is only understood by reasoning models (gpt-oss family).
    if LLM_REASONING_EFFORT and "gpt-oss" in model:
        return {"reasoning_effort": LLM_REASONING_EFFORT}
    return None


def _parse_duration(value: str) -> float | None:
    """Parse Groq-style durations: '2.15s', '1m3.5s', '450ms'."""
    total, matched = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value):
        matched = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if matched else None


def _retry_after(exc: openai.APIStatusError) -> float:
    headers = getattr(exc.response, "headers", {}) or {}
    for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            parsed = _parse_duration(raw)
            if parsed is not None:
                return parsed
    return 10.0


def _record_usage(stats: dict | None, agent: str, usage: Any):
    if stats is None or usage is None:
        return
    entry = stats.setdefault(agent, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    entry["calls"]             += 1
    entry["prompt_tokens"]     += getattr(usage, "prompt_tokens", 0) or 0
    entry["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0


async def _with_retries(call, *, agent: str, emit: Emit | None):
    if not LLM_API_KEY:
        raise LLMUnavailable("LLM_API_KEY is not configured")

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await call()
        except openai.RateLimitError as exc:
            wait = _retry_after(exc) + 0.5
            if wait > _MAX_RATE_LIMIT_WAIT:
                raise QuotaExhausted(f"rate limit quota exhausted (retry in {wait:.0f}s)", wait) from exc
            logger.info(f"LLM 429 ({agent}) — waiting {wait:.1f}s")
            if emit:
                await emit("agent_log", {"agent": agent, "type": "start",
                    "msg": f"LLM provider rate limit — waiting {wait:.0f}s"})
            await asyncio.sleep(wait)
        except openai.BadRequestError as exc:
            # The model occasionally emits a malformed tool call; a retry usually succeeds.
            if "tool_use_failed" in str(exc) and attempt < 3:
                logger.warning(f"LLM produced a malformed tool call ({agent}) — retrying")
                continue
            raise LLMUnavailable(f"bad request: {str(exc)[:200]}") from exc
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
            if attempt < 3:
                await asyncio.sleep(3)
                continue
            raise LLMUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code == 413:
                raise ContextTooLarge(f"request too large: {str(exc)[:200]}") from exc
            raise LLMUnavailable(f"HTTP {exc.status_code}: {str(exc)[:200]}") from exc
    raise LLMUnavailable("rate limit retries exhausted")


async def _with_fallback(make_call, *, model: str, agent: str, emit: Emit | None):
    """Try the model, then each fallback model whose quota is not known to be exhausted.

    ``make_call(model)`` returns the zero-argument coroutine factory for one model.
    Returns (response, model_used).
    """
    last_exc: QuotaExhausted | None = None
    for candidate in _model_chain(model):
        try:
            return await _with_retries(make_call(candidate), agent=agent, emit=emit), candidate
        except QuotaExhausted as exc:
            _exhausted_until[candidate] = time.monotonic() + exc.retry_after
            last_exc = exc
            logger.warning(f"{candidate} quota exhausted ({agent}) — trying the next model")
            if emit:
                await emit("agent_log", {"agent": agent, "type": "start",
                    "msg": f"{candidate} quota exhausted — switching to the next configured model"})
    raise last_exc or LLMUnavailable("no model available")


async def chat(messages: list[dict], *, model: str, agent: str,
               tools: list[dict] | None = None, tool_choice: Any = None,
               max_tokens: int = 1024, temperature: float = 0.1,
               emit: Emit | None = None, stats: dict | None = None):
    """One chat completion. Returns the assistant message (may contain tool_calls)."""
    kwargs: dict[str, Any] = dict(messages=messages, max_tokens=max_tokens, temperature=temperature)
    if tools:
        kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

    def make_call(m: str):
        async def call():
            return await _get_client().chat.completions.create(model=m, **kwargs, extra_body=_extra_body(m))
        return call

    resp, _ = await _with_fallback(make_call, model=model, agent=agent, emit=emit)
    _record_usage(stats, agent, resp.usage)
    return resp.choices[0].message


async def stream_llm(system: str, user: str, emit: Emit, agent: str, *, model: str,
                     max_tokens: int = 400, temperature: float = 0.2,
                     stats: dict | None = None) -> str:
    """Stream a plain-text answer to the UI. Raises LLMUnavailable on failure."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def make_call(m: str):
        async def call():
            return await _get_client().chat.completions.create(
                model=m, messages=messages, max_tokens=max_tokens, temperature=temperature,
                stream=True, stream_options={"include_usage": True}, extra_body=_extra_body(m),
            )
        return call

    stream, _ = await _with_fallback(make_call, model=model, agent=agent, emit=emit)
    await emit("llm_start", {"agent": agent})
    full = ""
    try:
        async for chunk in stream:
            if chunk.usage:
                _record_usage(stats, agent, chunk.usage)
            token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if token:
                full += token
                await emit("llm_token", {"agent": agent, "token": token})
    finally:
        await emit("llm_done", {"agent": agent})
    if not full.strip():
        raise LLMUnavailable("empty response")
    return full.strip()
