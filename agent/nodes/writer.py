"""writer: one Claude Haiku call that streams the answer from `facts` only,
then a <<SUGGESTIONS>> block that is turned into the UI's `suggestions` event.

Text is streamed to the UI as custom events while it is generated; the marker
and what follows it are held back, even when the marker is split across chunks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.config import get_stream_writer

from ..grounding import ungrounded
from ..prompts import OFF_TOPIC_REPLY, SUGGESTIONS_MARKER, WRITER_SYSTEM
from ..state import AgentState
from .common import transcript

log = logging.getLogger(__name__)


def parse_suggestions(tail: str) -> list[str]:
    out: list[str] = []
    for raw in tail.replace("|", "\n").splitlines():
        line = raw.strip().lstrip("-*•0123456789.) ").strip().strip('"')
        if line and "<<" not in line:
            out.append(line)
        if len(out) == 3:
            break
    return out


def writer_facts(state: AgentState) -> dict[str, Any]:
    """Exactly what the writer sees; the grounding check uses the same dict."""
    facts = dict(state.get("facts") or {})
    if state.get("property_context"):
        facts.setdefault("open_listing", state["property_context"])
    return facts


def _user_message(state: AgentState, facts: dict[str, Any]) -> str:
    parts = ["FACTS:", json.dumps(facts, ensure_ascii=False, indent=1, default=str)]
    if conv := transcript(state):
        parts += ["", "CONVERSATION SO FAR:", conv]
    parts += [
        "",
        f"QUESTION: {state['prompt']}",
        "",
        "(Answer from FACTS only. Every AED amount you write must appear in FACTS "
        "exactly: no differences, totals, ranges or other arithmetic.)",
    ]
    return "\n".join(parts)


async def writer(state: AgentState, *, stream_text) -> dict[str, Any]:
    emit = get_stream_writer()
    buffer, sent, marker_at = "", 0, -1
    hold = len(SUGGESTIONS_MARKER) - 1
    call: dict[str, Any] = {}

    facts = writer_facts(state)
    async for delta in stream_text(WRITER_SYSTEM, _user_message(state, facts), call):
        buffer += delta
        if marker_at == -1:
            marker_at = buffer.find(SUGGESTIONS_MARKER)
        end = marker_at if marker_at != -1 else len(buffer) - hold
        if end > sent:
            emit({"event_type": "chunk", "content": buffer[sent:end]})
            sent = end

    if marker_at == -1:
        if len(buffer) > sent:
            emit({"event_type": "chunk", "content": buffer[sent:]})
        answer, suggestions = buffer.strip(), []
    else:
        answer = buffer[:marker_at].strip()
        suggestions = parse_suggestions(buffer[marker_at + len(SUGGESTIONS_MARKER) :])
    if suggestions:
        emit({"event_type": "suggestions", "content": suggestions})

    user_text = state["prompt"] + "\n" + transcript(state)
    mismatches = ungrounded(answer, facts, user_text)
    if mismatches:
        log.warning("ungrounded AED figures: %s", mismatches)
    return {
        "answer": answer,
        "suggestions": suggestions,
        "trace": [{"node": "writer", **call, "ungrounded": mismatches}],
    }


def off_topic(state: AgentState) -> dict[str, Any]:
    """Fixed reply, no model call (spec §5)."""
    get_stream_writer()({"event_type": "chunk", "content": OFF_TOPIC_REPLY})
    return {"answer": OFF_TOPIC_REPLY, "suggestions": [], "trace": [{"node": "off_topic"}]}
