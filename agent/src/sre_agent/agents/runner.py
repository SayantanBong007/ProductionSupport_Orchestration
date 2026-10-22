"""
The tool-using agent loop.

run_agent() is the reason → act → observe loop every agent runs: the LLM
decides which tools to call (several at once run in parallel), sees the
results, and decides when it is done. Observations are written to the
evidence ledger so later claims can be checked against them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sre_agent.config import MAX_CONTEXT_CHARS
from sre_agent.core.evidence import EvidenceLedger
from sre_agent.core.llm import ContextTooLarge, Emit, LLMUnavailable, chat
from sre_agent.tools import Tool

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """The agent could not reach a conclusion."""


@dataclass
class AgentRun:
    text: str                  # final plain-text answer (or last reasoning text)
    final_args: dict | None    # arguments of the final tool call, if one was required
    messages: list             # full conversation — lets a caller resume the agent
    rounds: int


def _assistant_dict(msg) -> dict:
    d: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                           for c in msg.tool_calls]
    return d


def _parse_args(raw: str | None) -> dict | str:
    try:
        args = json.loads(raw or "{}")
        return args if isinstance(args, dict) else "arguments must be a JSON object"
    except ValueError as exc:
        return f"arguments are not valid JSON: {exc}"


def compact(msgs: list[dict], budget_chars: int):
    """Shrink the oldest tool outputs until the conversation fits the budget.

    Providers cap the size of a single request, and an agent's conversation
    grows with every tool result. Older observations are cut first; the
    evidence ledger still holds them in full for verification.
    """
    size = lambda m: len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or ""))
    total = sum(size(m) for m in msgs)
    for m in msgs:
        if total <= budget_chars:
            return
        content = m.get("content") or ""
        if m["role"] == "tool" and len(content) > 400 and not content.endswith("[compacted]"):
            cut = content[:300] + "\n… [compacted]"
            total -= len(content) - len(cut)
            m["content"] = cut

    # Still too big: summarise long arguments of earlier tool calls (e.g. a previous
    # diagnosis submission). The last two messages are left intact.
    for m in msgs[:-2]:
        if total <= budget_chars:
            return
        for call in m.get("tool_calls") or []:
            args = call["function"]["arguments"]
            if len(args) > 600:
                short = json.dumps({"compacted": args[:200]})
                total -= len(args) - len(short)
                call["function"]["arguments"] = short


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={json.dumps(v)[:80]}" for k, v in args.items())


async def _execute(call, tools: dict[str, Tool], ledger: EvidenceLedger, emit: Emit, agent: str) -> str:
    name = call.function.name
    tool = tools.get(name)
    args = _parse_args(call.function.arguments)
    if tool is None:
        return f"error: unknown tool '{name}'. Available: {', '.join(tools)}"
    if isinstance(args, str):
        return f"error: {args}"
    # Models sometimes send placeholder keys (e.g. {"": ""}) for parameterless tools.
    args = {k: v for k, v in args.items() if k in tool.parameters.get("properties", {})}

    if tool.records_evidence:
        await emit("agent_log", {"agent": agent, "type": "step", "msg": f"{name}({_fmt_args(args)})"})
    try:
        output = await tool.fn(**args)
    except TypeError as exc:
        output = f"error: bad arguments for {name}: {exc}"
    except Exception as exc:  # a failing tool is itself an observation
        logger.warning(f"tool {name} failed: {exc}")
        output = f"error: {type(exc).__name__}: {exc}"

    if not tool.records_evidence:
        return output
    ev = ledger.add(agent, name, args, output)
    first = output.strip().splitlines()[0] if output.strip() else "(empty)"
    await emit("agent_log", {"agent": agent, "type": "result", "msg": f"[{ev.id}] {first[:220]}"})
    return f"[{ev.id}] {name}({_fmt_args(args)})\n{output}"


async def run_agent(*, name: str, model: str, tools: list[Tool], ledger: EvidenceLedger, emit: Emit,
                    max_rounds: int, system: str = "", user: str = "", final_tool: Tool | None = None,
                    messages: list | None = None, max_tokens: int = 1024,
                    stats: dict | None = None) -> AgentRun:
    """Run one agent until it answers (or calls ``final_tool``) or exhausts its rounds.

    Pass ``messages`` to resume an earlier run (e.g. with reviewer feedback appended).
    """
    msgs = messages if messages is not None else [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]
    all_tools = tools + ([final_tool] if final_tool else [])
    by_name = {t.name: t for t in all_tools}
    schemas = [t.schema() for t in all_tools]

    # Agents with a final tool get grace rounds: on the last rounds every tool
    # stays declared (the provider rejects calls to undeclared tools), but only
    # the final tool is executed.
    last_round = max_rounds + (2 if final_tool else 0)
    for rnd in range(1, last_round + 1):
        budget_spent = rnd >= max_rounds
        if not budget_spent:
            round_tools, choice = schemas, "auto"
        elif final_tool:
            round_tools, choice = schemas, "required"
        else:
            round_tools, choice = None, None
            msgs.append({"role": "user", "content": "Tool budget used up — write your findings now "
                                                    "from the evidence above."})

        compact(msgs, MAX_CONTEXT_CHARS)
        try:
            msg = await chat(msgs, model=model, agent=name, tools=round_tools, tool_choice=choice,
                             max_tokens=max_tokens, emit=emit, stats=stats)
        except ContextTooLarge:
            compact(msgs, MAX_CONTEXT_CHARS // 2)
            msg = await chat(msgs, model=model, agent=name, tools=round_tools, tool_choice=choice,
                             max_tokens=max_tokens, emit=emit, stats=stats)
        except LLMUnavailable as exc:
            # The provider rejects a reply that ignores tool_choice="required";
            # ask again without forcing — the nudge below handles a text reply.
            if choice != "required" or "tool_use_failed" not in str(exc):
                raise
            msg = await chat(msgs, model=model, agent=name, tools=round_tools, tool_choice="auto",
                             max_tokens=max_tokens, emit=emit, stats=stats)
        msgs.append(_assistant_dict(msg))
        calls = msg.tool_calls or []
        thought = (msg.content or "").strip()

        if not calls:
            if final_tool:
                msgs.append({"role": "user",
                             "content": f"Submit your conclusion by calling {final_tool.name}."})
                continue
            return AgentRun(thought, None, msgs, rnd)

        if thought:
            await emit("agent_log", {"agent": name, "type": "start", "msg": thought[:400]})

        final_call = next((c for c in calls if final_tool and c.function.name == final_tool.name), None)
        if final_call:
            args = _parse_args(final_call.function.arguments)
            for c in calls:
                if c is final_call:
                    content = f"error: {args}" if isinstance(args, str) else "received"
                else:
                    content = "skipped: a final answer was submitted in the same turn"
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": content})
            if isinstance(args, str):
                continue
            return AgentRun(thought, args, msgs, rnd)

        if budget_spent and final_tool:
            for c in calls:
                msgs.append({"role": "tool", "tool_call_id": c.id,
                             "content": f"not executed: tool budget exhausted. Call {final_tool.name} "
                                        "now with what you have already observed."})
            continue

        outputs = await asyncio.gather(*[_execute(c, by_name, ledger, emit, name) for c in calls])
        for c, out in zip(calls, outputs):
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": out})

    raise AgentError(f"{name} did not reach a conclusion within {max_rounds} rounds")
