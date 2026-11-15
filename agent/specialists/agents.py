"""Building and running one Strands agent for a question (spec §5).

make_agent() is the only place a Strands Agent is created; tests pass a fake
with the same stream_async() events, so they need no AWS.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

from langgraph.config import get_stream_writer

from .. import config, memory
from ..nodes.common import history
from ..prompts import agent_system
from ..state import AgentState
from .gate import OutputGate
from .tools import ToolBox, tool_names


class AgentLike(Protocol):
    def stream_async(self, prompt: Any, **kwargs: Any) -> Any: ...


MakeAgent = Callable[[str, list[Callable], list[dict[str, Any]]], AgentLike]


@lru_cache(maxsize=2)
def _model():
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=config.writer_model_id(),
        region_name=config.region(),
        max_tokens=config.writer_max_tokens(),
        temperature=0.3,
        streaming=True,
    )


def make_agent(
    system_prompt: str, tools: list[Callable], messages: list[dict[str, Any]]
) -> AgentLike:
    """A fresh Strands agent per question: Claude Haiku 4.5 on Bedrock, this question's
    tools, and the conversation so far (from the checkpointer) as its messages."""
    from strands import Agent

    return Agent(
        model=_model(),
        system_prompt=system_prompt,
        tools=tools,
        messages=messages,
        callback_handler=None,
        name="dubainest-specialist",
    )


def strands_messages(state: AgentState) -> list[dict[str, Any]]:
    """The last turns as Strands messages: start with user, alternate, end with assistant."""
    msgs: list[dict[str, Any]] = []
    for t in history(state):
        role = "assistant" if t.get("role") == "assistant" else "user"
        text = (t.get("content") or "").strip()[:1500]
        if not text or (not msgs and role == "assistant"):
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    return msgs


def visitor_words(state: AgentState) -> str:
    """What the visitor typed: this question and their earlier turns."""
    turns = [t["content"] for t in history(state) if t.get("role") == "user"]
    return "\n".join([*turns, state["prompt"]])


def earlier_answers(state: AgentState) -> str:
    return "\n".join(t["content"] for t in history(state) if t.get("role") == "assistant")


def user_message(state: AgentState) -> str:
    parts = []
    if pc := state.get("property_context"):
        brief = {k: pc[k] for k in ("id", "title", "area", "price_aed", "link") if k in pc}
        parts.append(f"Listing open in the UI: {json.dumps(brief, ensure_ascii=False)}")
    q = state["prompt"]
    known = memory.describe(
        state.get("profile"),
        with_budget=memory.mentions_budget(q),
        with_preferences=memory.refers_to_visitor(q),
    )
    if known:
        parts.append(f"Known about this visitor from earlier visits: {known}")
    if not memory.refers_to_visitor(q):
        # Placed next to the question: a rule in the system prompt alone loses to a
        # chat that just discussed the budget.
        parts.append(
            "Answer this on its own terms: don't compare with the visitor's budget or "
            "earlier preferences unless the question asks."
        )
    parts.append(f"Question: {state['prompt']}")
    return "\n".join(parts)


async def run_specialist(state: AgentState, *, make: MakeAgent, node: str) -> dict[str, Any]:
    intents = list(state.get("intents") or ["general"])
    words = visitor_words(state)
    box = ToolBox(words, state.get("property_context"))
    agent = make(agent_system(intents), box.build(tool_names(intents)), strands_messages(state))
    gate = OutputGate(
        get_stream_writer(),
        box.facts,
        words,
        context={
            "open_listing": state.get("property_context"),
            "profile": state.get("profile"),
            # Earlier answers passed this gate when they were sent, so the figures in
            # them were verified then (e.g. a price found by last turn's search).
            "earlier_answers": earlier_answers(state),
        },
    )
    cancel = threading.Event()
    result = None
    t0 = time.perf_counter()
    async for ev in agent.stream_async(
        user_message(state),
        cancel_signal=cancel,
        limits={"turns": config.agent_max_turns()},
    ):
        if "data" in ev:
            gate.on_text(ev["data"])
        elif "current_tool_use" in ev:
            gate.on_tool(ev["current_tool_use"])
        elif "message" in ev and ev["message"].get("role") == "assistant":
            content = ev["message"].get("content") or []
            gate.on_turn_end(any("toolUse" in c for c in content))
        elif "result" in ev:
            result = ev["result"]
        if gate.stopped and not cancel.is_set():
            cancel.set()  # a blocked answer: stop spending tokens on the rest
    gate.finish()

    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}
    calls = getattr(getattr(result, "metrics", None), "cycle_count", None) or 1
    if box.pending_viewing and gate.stopped:
        pending = None  # the visitor never saw the details, so a "yes" must not send them
    elif box.pending_viewing:
        pending = box.pending_viewing
    elif "request_viewing" in box.calls:
        pending = None  # a new, still incomplete request replaces the old one
    else:
        pending = state.get("pending_viewing")
    return {
        "facts": box.facts,
        "answer": gate.answer.strip(),
        "suggestions": gate.suggestions,
        "seen_ids": box.seen_ids,
        "pending_viewing": pending,
        "trace": [
            {
                "node": node,
                "framework": "strands",
                "model": config.writer_model_id(),
                "model_calls": calls,
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
                "ms": round((time.perf_counter() - t0) * 1000),
                "tools": box.calls,
                "stop_reason": getattr(result, "stop_reason", None),
                "ungrounded": gate.blocked,
                "narration_dropped": gate.dropped_narration,
            }
        ],
    }
