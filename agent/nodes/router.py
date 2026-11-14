"""router: one Nova Micro call → 1-2 intents. Keyword router when unsure or on error."""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from ..prompts import CONFIRM_PHRASE, ROUTER_SYSTEM
from ..state import AgentState, Intent
from ..tools import listings
from .common import AFFIRMATIVE, context_text, last

log = logging.getLogger(__name__)

# Ported from dubainest-ai orchestrator.py, with a few words added. Order = priority.
_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    (
        "scheduler",
        (
            "schedule",
            "viewing",
            "visit",
            "book",
            "appointment",
            "tour",
            "see the property",
            "open house",
            "meeting",
        ),
    ),
    (
        "roi",
        (
            "roi",
            "yield",
            "rental income",
            "rent ",
            "investment return",
            "return on",
            "cashflow",
            "cash flow",
            "payback",
            "mortgage",
            "break-even",
            "break even",
        ),
    ),
    (
        "market_intel",
        (
            "market",
            "trend",
            "demand",
            "supply",
            "growth",
            "forecast",
            "golden visa",
            "appreciation",
            "off-plan vs",
            "neighbourhood",
            "neighborhood",
            "which area",
        ),
    ),
    (
        "property_search",
        (
            "property",
            "properties",
            "apartment",
            "villa",
            "townhouse",
            "penthouse",
            "studio",
            "bedroom",
            "br ",
            "listing",
            "price",
            "developer",
            "buy",
            "sqft",
            "show me",
            "find",
            "damac",
            "emaar",
            "nakheel",
        ),
    ),
]


# Two-specialist fan-out only for combinations that make sense together. The
# scheduler is never a second intent: it would make the answer ask for viewing details.
_PAIRS = {
    frozenset(p)
    for p in (
        ("property_search", "roi"),
        ("property_search", "market_intel"),
        ("market_intel", "roi"),
    )
}


def keyword_route(text: str) -> Intent:
    low = f" {text.lower()} "
    for intent, words in _KEYWORDS:
        if any(w in low for w in words):
            return intent
    if listings.parse_filters(text).get("areas") or listings.mentioned_listings(text):
        return "property_search"
    return "general"


def _router_user_message(state: AgentState) -> str:
    parts = []
    if state.get("property_context"):
        parts.append(f"Listing open in the UI: {state['property_context']['title']}")
    prev_user, prev_bot = last(state, "user"), last(state, "assistant")
    if prev_user:
        parts.append(f"Previous user turn: {prev_user[:300]}")
    if prev_bot:
        parts.append(f"Previous assistant turn: {prev_bot[:300]}")
    parts.append(f"Question: {state['prompt']}")
    return "\n".join(parts)


async def router(state: AgentState, *, classify, min_confidence: float) -> dict[str, Any]:
    prompt = state["prompt"]
    open_listing = listings.get(state.get("property_id"))
    update: dict[str, Any] = {
        "property_context": listings.summary(open_listing) if open_listing else None
    }
    state = cast(AgentState, {**state, **update})

    # A "yes" right after the viewing confirmation is always the scheduler (no model call).
    if CONFIRM_PHRASE in last(state, "assistant") and AFFIRMATIVE.match(prompt):
        return {
            **update,
            "intents": ["scheduler"],
            "trace": [{"node": "router", "rule": "viewing_confirmation"}],
        }

    try:
        route, call = await classify(ROUTER_SYSTEM, _router_user_message(state))
    except Exception as exc:  # noqa: BLE001 — any model failure falls back to keywords
        log.warning("router model failed, using keywords: %s", exc)
        intent = keyword_route(context_text(state))
        return {
            **update,
            "intents": [intent],
            "trace": [
                {"node": "router", "rule": "keywords_after_error", "error": type(exc).__name__}
            ],
        }

    entry = {
        "node": "router",
        **call,
        "intent": route.intent,
        "second_intent": route.second_intent,
        "confidence": route.confidence,
    }
    if route.confidence < min_confidence and route.intent != "off_topic":
        intent = keyword_route(context_text(state))
        entry["rule"] = f"keywords_low_confidence→{intent}"
        return {**update, "intents": [intent], "trace": [entry]}

    intents: list[Intent] = [route.intent]
    if frozenset((route.intent, route.second_intent)) in _PAIRS:
        intents.append(route.second_intent)  # type: ignore[arg-type]
    elif route.second_intent and route.second_intent != route.intent:
        entry["dropped_second_intent"] = route.second_intent
    return {**update, "intents": intents, "trace": [entry]}


def next_nodes(state: AgentState) -> list[str]:
    """Conditional edge: off_topic ends the graph; everything else goes to its specialist(s)."""
    intents = state.get("intents") or ["general"]
    if "off_topic" in intents:
        return ["off_topic"]
    return [i for i in intents if re.fullmatch(r"[a-z_]+", i)]
