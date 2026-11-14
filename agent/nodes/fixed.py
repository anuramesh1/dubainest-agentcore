"""The two nodes that answer without a model: confirm_viewing and off_topic."""

from __future__ import annotations

import re
from typing import Any

from langgraph.config import get_stream_writer

from ..prompts import OFF_TOPIC_REPLY
from ..state import AgentState
from ..tools import leads, listings
from .common import AFFIRMATIVE

NEGATIVE = re.compile(r"^\s*(no|nope|nah|cancel|stop|not now|don'?t|never ?mind)\b", re.I)


def _say(text: str, suggestions: list[str]) -> None:
    emit = get_stream_writer()
    emit({"event_type": "chunk", "content": text})
    if suggestions:
        emit({"event_type": "suggestions", "content": suggestions})


def confirm_viewing(state: AgentState) -> dict[str, Any]:
    """ "yes" to a pending viewing request writes it to Leads; "no" cancels it.
    The only place in the graph that writes a lead (spec §10)."""
    pv = state.get("pending_viewing")
    if pv and AFFIRMATIVE.match(state["prompt"]):
        lead_id = leads.save_viewing_request(pv)
        ref = lead_id[:8].upper()
        p = listings.get(pv["property_id"])
        name = listings.project_name(p) if p else pv["property_title"]
        text = (
            "**Your viewing request has been sent to the DubaiNest team.**\n"
            f"- **{pv['property_title']}**, {pv['when']}\n"
            f"- Reference: **{ref}**\n\n"
            "The team will contact you to confirm a slot. It's a request, not a booking yet."
        )
        suggestions = [f"What's the ROI on {name}?", f"Show me listings similar to {name}"]
        status, facts = "saved", {"reference": ref}
    elif pv:
        text = "No problem, I've cancelled that viewing request. Anything else I can help with?"
        suggestions = ["Show me other listings", "Arrange a different viewing"]
        status, facts = "cancelled", {}
    else:
        text = "There's no viewing request waiting for confirmation. Which property would you like to see?"
        suggestions = ["Arrange a viewing", "Show me listings"]
        status, facts = "nothing_pending", {}
    _say(text, suggestions)
    return {
        "answer": text,
        "suggestions": suggestions,
        "pending_viewing": None,
        "facts": {"viewing": {"status": status, **facts}},
        "trace": [{"node": "confirm_viewing", "status": status}],
    }


def off_topic(state: AgentState) -> dict[str, Any]:
    """Fixed reply, no model call (spec §5)."""
    _say(OFF_TOPIC_REPLY, [])
    return {"answer": OFF_TOPIC_REPLY, "suggestions": [], "trace": [{"node": "off_topic"}]}
