"""load_memory and remember: the graph's first and last nodes (spec §5, §7).

The conversation itself (history, pending viewing) is persisted by the
checkpointer for the chat session; these nodes handle the long-term visitor
profile in the LangGraph store and keep the conversation short.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_store

from .. import memory
from ..state import AgentState, Turn
from ..tools import listings

log = logging.getLogger(__name__)
KEEP_TURNS = 12  # the agents see the last 6


async def load_memory(state: AgentState) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    vid = state.get("visitor_id")
    if vid:
        try:
            item = await get_store().aget(memory.namespace(vid), memory.PROFILE_KEY)
            profile = dict(item.value) if item else {}
        except Exception as exc:  # noqa: BLE001 — memory must never break an answer
            log.warning("could not load visitor profile: %s", exc)
    hist = list(state.get("history") or [])
    seeded = False
    if not hist and state.get("request_history"):
        hist = [
            Turn(role=t.get("role", ""), content=t.get("content", ""))
            for t in state["request_history"]
            if t.get("role") in ("user", "assistant") and (t.get("content") or "").strip()
        ][-KEEP_TURNS:]
        seeded = bool(hist)
    open_listing = listings.get(state.get("property_id"))
    return {
        "profile": profile,
        "history": hist,
        "property_context": listings.summary(open_listing) if open_listing else None,
        "trace": [
            {
                "node": "load_memory",
                "history_turns": len(hist),
                "history_from_browser": seeded,
                "profile_keys": sorted(k for k in profile if k != "updated_at"),
            }
        ],
    }


async def remember(state: AgentState) -> dict[str, Any]:
    turns = [
        *(state.get("history") or []),
        {"role": "user", "content": state["prompt"]},
        {"role": "assistant", "content": state.get("answer") or ""},
    ][-KEEP_TURNS:]
    old = state.get("profile") or {}
    new = memory.update_profile(old, state["prompt"], state.get("seen_ids"))
    saved = False
    vid = state.get("visitor_id")
    if vid and new != old:
        try:
            await get_store().aput(memory.namespace(vid), memory.PROFILE_KEY, new)
            saved = True
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save visitor profile: %s", exc)
    return {
        "history": turns,
        "profile": new,
        "trace": [{"node": "remember", "profile_saved": saved}],
    }
