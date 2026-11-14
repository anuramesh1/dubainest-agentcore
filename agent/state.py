"""Graph state (spec §5)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Intent = Literal["property_search", "market_intel", "roi", "scheduler", "general", "off_topic"]
SPECIALISTS: tuple[Intent, ...] = ("property_search", "market_intel", "roi", "scheduler")


def merge_facts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Specialists running in parallel each add their own key to `facts`."""
    return {**(left or {}), **(right or {})}


class Turn(TypedDict):
    role: str  # "user" | "assistant"
    content: str


class AgentState(TypedDict, total=False):
    # input
    prompt: str
    property_id: str | None
    history: list[Turn]
    locale: str
    # derived
    property_context: dict[str, Any] | None  # the listing open in the UI, if any
    intents: list[Intent]  # 1 or 2 (two-intent questions fan out)
    facts: Annotated[dict[str, Any], merge_facts]  # specialist results; the writer's only data
    answer: str
    suggestions: list[str]
    trace: Annotated[list[dict[str, Any]], operator.add]  # one entry per node / model call
