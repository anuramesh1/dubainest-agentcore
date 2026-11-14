"""Graph state (spec §5).

With the checkpointer, state persists per chat session (thread_id). Fields that
belong to one turn (facts, trace, answer, ...) are reset by each request's
input; `history`, `pending_viewing` and `profile` carry over.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

Intent = Literal["property_search", "market_intel", "roi", "scheduler", "general", "off_topic"]
SPECIALISTS: tuple[Intent, ...] = ("property_search", "market_intel", "roi", "scheduler")


def add_trace(left: list | None, right: list | None) -> list:
    """Append entries; None (sent with each new request) starts a fresh trace."""
    if right is None:
        return []
    return [*(left or []), *right]


class Turn(TypedDict):
    role: str  # "user" | "assistant"
    content: str


class AgentState(TypedDict, total=False):
    # input, per request
    prompt: str
    property_id: str | None
    visitor_id: str | None
    locale: str
    request_history: list[Turn]  # what the browser sent; seeds `history` for a new session
    # conversation memory (checkpointer, per session)
    history: list[Turn]
    pending_viewing: dict[str, Any] | None  # held by request_viewing until "yes"
    # long-term memory (store, per visitor), loaded each turn
    profile: dict[str, Any]
    # this turn
    property_context: dict[str, Any] | None  # the listing open in the UI, if any
    intents: list[Intent]  # 1, or 2 when the router finds a sensible pair
    route: str  # the node the router chose: an agent, confirm_viewing or off_topic
    facts: dict[str, Any]  # every tool result this turn; the output gate checks against it
    seen_ids: list[str]  # listings the agent looked up this turn
    answer: str
    suggestions: list[str]
    trace: Annotated[list[dict[str, Any]], add_trace]  # one entry per node / model call
