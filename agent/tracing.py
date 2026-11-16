"""The agent trace the chat UI shows under an answer.

Every graph node appends a raw entry to `state["trace"]` (agent/state.py). build()
turns those entries into ordered steps a visitor can read:

    {"kind": "route", "label": "intent → roi · node → roi_agent",
     "detail": {"model": "nova-micro", "confidence": 0.95}, "ms": 412}

Kinds: guardrail, memory, route, agent, tool, gate.

The site is public, so a step carries names, counts, timings and token counts
only: never prompts, tool arguments or results, profile values or error messages.
"""

from __future__ import annotations

import re
from typing import Any

_VERSION = re.compile(r"(-\d{8})?(-v\d+(:\d+)?)?$")


def short_model(model_id: str | None) -> str:
    """us.anthropic.claude-haiku-4-5-20251001-v1:0 → claude-haiku-4-5"""
    name = (model_id or "").split(".")[-1].split("/")[-1]
    return _VERSION.sub("", name) or (model_id or "")


def _step(kind: str, label: str, detail: dict[str, Any] | None = None, ms: Any = 0) -> dict:
    try:
        took = round(float(ms or 0), 1)
    except (TypeError, ValueError):
        took = 0.0
    return {"kind": kind, "label": label, "detail": detail or {}, "ms": took}


def _guard_steps(guard: Any) -> list[dict]:
    """The API's checks (api/chat_relay.py). A question that reached the agent passed both."""
    if not isinstance(guard, dict):
        return []
    return [
        _step("guardrail", "layer 1: injection screen", {"passed": True}, guard.get("screen_ms")),
        _step("guardrail", "rate and daily caps", {"passed": True}, guard.get("limits_ms")),
    ]


def _route_step(entry: dict[str, Any], intents: list[str], route: str) -> dict:
    label = f"intent → {' + '.join(intents) or 'unknown'} · node → {route or 'unknown'}"
    detail: dict[str, Any] = {}
    if entry.get("model"):
        detail["model"] = short_model(entry["model"])
        detail["confidence"] = entry.get("confidence")
    else:
        detail["model"] = "none (rule)"
    for key in ("rule", "error", "dropped_second_intent"):
        if entry.get(key):
            detail[key] = entry[key]
    return _step("route", label, detail, entry.get("ms"))


def _agent_steps(entry: dict[str, Any]) -> list[dict]:
    steps = [
        _step(
            "agent",
            f"Strands → {entry.get('node')}",
            {
                "model": short_model(entry.get("model")),
                "model_calls": entry.get("model_calls"),
                "tokens_in": entry.get("input_tokens"),
                "tokens_out": entry.get("output_tokens"),
                "stop": entry.get("stop_reason"),
            },
            entry.get("ms"),
        )
    ]
    calls = entry.get("tool_calls")
    if calls is None:  # an entry without the timeline: names only
        calls = [{"name": n} for n in entry.get("tools") or []]
    for c in calls:
        detail = {"status": c.get("status", "ok"), **(c.get("summary") or {})}
        steps.append(_step("tool", f"call {c.get('name')}", detail, c.get("ms")))
    blocked = list(entry.get("ungrounded") or [])
    steps.append(
        _step(
            "gate",
            (
                "output gate: blocked an unverified figure"
                if blocked
                else "output gate: figures grounded"
            ),
            {"blocked": blocked, "narration_dropped": entry.get("narration_dropped", 0)},
        )
    )
    return steps


def build(
    final: dict[str, Any], guard: Any = None, total_ms: float | None = None
) -> dict[str, Any]:
    """The `trace` event's content, from the graph's final state."""
    entries = [e for e in (final.get("trace") or []) if isinstance(e, dict)]
    intents = [str(i) for i in (final.get("intents") or [])]
    route = str(final.get("route") or "")
    steps = _guard_steps(guard)
    tools: list[str] = []
    for e in entries:
        node = e.get("node")
        if node == "load_memory":
            steps.append(
                _step(
                    "memory",
                    "load conversation and visitor profile",
                    {
                        "history_turns": e.get("history_turns", 0),
                        "history_from_browser": e.get("history_from_browser", False),
                        "profile_keys": e.get("profile_keys") or [],
                    },
                )
            )
        elif node == "router":
            steps.append(_route_step(e, intents, route))
        elif e.get("framework") == "strands":
            steps += _agent_steps(e)
            tools = list(dict.fromkeys([*tools, *(e.get("tools") or [])]))  # each name once
        elif node == "confirm_viewing":
            steps.append(
                _step("gate", "viewing confirmation (no model)", {"status": e.get("status")})
            )
        elif node == "off_topic":
            steps.append(_step("gate", "off-topic: fixed reply (no model)"))
        elif node == "remember":
            steps.append(
                _step(
                    "memory",
                    "save conversation and visitor profile",
                    {"profile_saved": e.get("profile_saved", False)},
                )
            )
    if total_ms is None:
        total_ms = sum(s["ms"] for s in steps if s["kind"] != "tool")  # tools run inside the agent
    return {
        "intents": intents,
        "agent": route,
        "tools": tools,
        "steps": steps,
        "total_ms": round(float(total_ms), 1),
        "model_calls": sum(e.get("model_calls", 1) for e in entries if "model" in e),
        "tokens_in": sum(int(e.get("input_tokens", 0) or 0) for e in entries),
        "tokens_out": sum(int(e.get("output_tokens", 0) or 0) for e in entries),
    }


def render(trace: dict[str, Any]) -> str:
    """Plain text, for the CLI."""
    lines = [
        f"intent: {' + '.join(trace['intents']) or '-'} · {trace['agent'] or '-'}"
        f" · tools: {', '.join(trace['tools']) or 'none'}"
    ]
    for s in trace["steps"]:
        detail = ", ".join(f"{k}={v}" for k, v in s["detail"].items() if v not in (None, "", False))
        took = f"{s['ms']:g}ms" if s["ms"] else ""
        lines.append(f"{s['kind']:<10}{s['label']}" + (f" {{{detail}}}" if detail else ""))
        if took:
            lines[-1] += f"  {took}"
    lines.append(
        f"{len(trace['steps'])} steps · {trace['total_ms']:g}ms · {trace['model_calls']} model "
        f"calls · {trace['tokens_in']} in / {trace['tokens_out']} out tokens"
    )
    return "\n".join(lines)
