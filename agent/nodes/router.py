"""router: one Nova Micro call → 1-2 intents → which node answers. Keyword router
when unsure or on error. A yes/no to a pending viewing request skips the model."""

from __future__ import annotations

import logging
from typing import Any, cast

from ..prompts import CONFIRM_PHRASE, ROUTER_SYSTEM
from ..state import AgentState, Intent
from ..tools import listings
from .common import AFFIRMATIVE, context_text, last
from .fixed import NEGATIVE

log = logging.getLogger(__name__)

# Keyword routing: the fallback when the model router fails or isn't confident. Order = priority.
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


# The node that answers each intent (Strands agents, spec §5).
AGENT_NODES: dict[str, str] = {
    "property_search": "property_agent",
    "market_intel": "market_agent",
    "roi": "roi_agent",
    "scheduler": "viewing_agent",
    "general": "general_agent",
}


def _routed(update: dict[str, Any], intents: list[Intent], entry: dict[str, Any]) -> dict[str, Any]:
    route = "off_topic" if "off_topic" in intents else AGENT_NODES.get(intents[0], "general_agent")
    # One line per question in the runtime logs, so a wrong route can be diagnosed.
    log.info(
        "route=%s intents=%s %s",
        route,
        intents,
        {k: entry[k] for k in ("rule", "intent", "second_intent", "confidence") if k in entry},
    )
    return {**update, "intents": intents, "route": route, "trace": [entry]}


async def router(state: AgentState, *, classify, min_confidence: float) -> dict[str, Any]:
    prompt = state["prompt"]
    update: dict[str, Any] = {}
    if "property_context" not in state:  # set by load_memory; the routing eval calls us directly
        open_listing = listings.get(state.get("property_id"))
        update["property_context"] = listings.summary(open_listing) if open_listing else None
    state = cast(AgentState, {**state, **update})

    # Yes / no to a viewing request held in conversation memory: no model call.
    if state.get("pending_viewing") and (AFFIRMATIVE.match(prompt) or NEGATIVE.match(prompt)):
        return {
            **update,
            "intents": ["scheduler"],
            "route": "confirm_viewing",
            "trace": [{"node": "router", "rule": "viewing_confirmation"}],
        }
    # "yes" after a confirmation we no longer hold (e.g. the session expired): ask again.
    if CONFIRM_PHRASE in last(state, "assistant") and AFFIRMATIVE.match(prompt):
        return _routed(
            update, ["scheduler"], {"node": "router", "rule": "confirmation_without_pending"}
        )

    try:
        route, call = await classify(ROUTER_SYSTEM, _router_user_message(state))
    except Exception as exc:  # noqa: BLE001 — any model failure falls back to keywords
        log.warning("router model failed, using keywords: %s", exc)
        intent = keyword_route(context_text(state))
        entry = {"node": "router", "rule": "keywords_after_error", "error": type(exc).__name__}
        return _routed(update, [intent], entry)

    entry = {
        "node": "router",
        **call,
        "intent": route.intent,
        "second_intent": route.second_intent,
        "confidence": route.confidence,
    }
    # A question that names a DubaiNest listing is never off-topic, whatever the
    # small router model says (a rare miss seen live on "What's the ROI on Burj Vista?").
    if route.intent == "off_topic" and listings.mentioned_listings(prompt):
        intent = keyword_route(prompt)
        if intent not in ("off_topic", "general"):
            entry["rule"] = f"named_listing_not_off_topic→{intent}"
            return _routed(update, [intent], entry)
    if route.confidence < min_confidence and route.intent != "off_topic":
        intent = keyword_route(context_text(state))
        entry["rule"] = f"keywords_low_confidence→{intent}"
        return _routed(update, [intent], entry)

    intents: list[Intent] = [route.intent]
    if frozenset((route.intent, route.second_intent)) in _PAIRS:
        intents.append(route.second_intent)  # type: ignore[arg-type]
    elif route.second_intent and route.second_intent != route.intent:
        entry["dropped_second_intent"] = route.second_intent
    return _routed(update, intents, entry)


def next_node(state: AgentState) -> str:
    """Conditional edge: the node the router chose."""
    return state.get("route") or "general_agent"
