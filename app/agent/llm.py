"""Provider-agnostic tool-calling loop.

`run_tool_loop` runs one agent turn: it calls the model, executes any tool calls
via `execute(name, args) -> dict`, feeds results back, and stops when the model
answers in text or the tool-call budget is spent (then it forces a final answer).

Providers: anthropic (tested), openai (untested: verify before relying on it).
"""
from __future__ import annotations

import json
from collections.abc import Callable

from app import config


class LLMError(RuntimeError):
    pass


def run_tool_loop(system: str, history: list[dict], tools: list[dict],
                  execute: Callable[[str, dict], dict],
                  max_calls: int = config.MAX_TOOL_CALLS_PER_TURN) -> str:
    """history: [{"role": "user"|"assistant", "content": str}]. Returns the final text."""
    if not config.llm_configured():
        raise LLMError("LLM not configured (set LLM_API_KEY and LLM_MODEL)")
    provider = config.LLM_PROVIDER
    try:
        if provider == "anthropic":
            return _anthropic(system, history, tools, execute, max_calls)
        if provider == "openai":
            return _openai(system, history, tools, execute, max_calls)
    except LLMError:
        raise
    except Exception as e:  # network, auth, rate limits: caller falls back to rules-only
        raise LLMError(f"{provider} call failed: {e}") from e
    raise LLMError(f"unsupported LLM_PROVIDER {provider!r} (use anthropic or openai)")


# --- Anthropic -------------------------------------------------------------------

def _anthropic(system, history, tools, execute, max_calls) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.LLM_API_KEY, timeout=60, max_retries=2)
    a_tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
               for t in tools]
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    calls = 0
    while True:
        budget_left = calls < max_calls
        kwargs = dict(model=config.LLM_MODEL, max_tokens=1500, system=system,
                      messages=messages, tools=a_tools)
        if not budget_left:
            kwargs["tool_choice"] = {"type": "none"}
        resp = client.messages.create(**kwargs)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses or not budget_left:
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        blocks = []
        for b in resp.content:
            if b.type == "text" and b.text:
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for tu in tool_uses:
            calls += 1
            if calls > max_calls:
                out = {"error": "tool-call limit reached; give your final AgentReply JSON now"}
            else:
                out = execute(tu.name, tu.input or {})
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)})
        messages.append({"role": "user", "content": results})


# --- OpenAI (untested) -----------------------------------------------------------

def _openai(system, history, tools, execute, max_calls) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.LLM_API_KEY, timeout=60, max_retries=2)
    o_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}} for t in tools]
    messages = [{"role": "system", "content": system}] + [dict(m) for m in history]
    calls = 0
    while True:
        budget_left = calls < max_calls
        resp = client.chat.completions.create(model=config.LLM_MODEL, messages=messages, tools=o_tools,
                                              tool_choice="auto" if budget_left else "none")
        msg = resp.choices[0].message
        if not msg.tool_calls or not budget_left:
            return (msg.content or "").strip()
        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            calls += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out = (execute(tc.function.name, args) if calls <= max_calls
                   else {"error": "tool-call limit reached; give your final AgentReply JSON now"})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(out, ensure_ascii=False, default=str)})
