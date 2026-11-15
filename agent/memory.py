"""Conversation memory and long-term memory, both through LangGraph (spec §7).

    conversation  checkpointer  DynamoDBSaver, thread_id = chat session, 24 h TTL
    long-term     store         DynamoDBStore, namespace ("visitor", visitor_id), 90 days

Without SESSIONS_TABLE / MEMORY_TABLE (local dev, tests, evals) the same graph
uses LangGraph's in-process InMemorySaver / InMemoryStore.

The visitor profile is extracted by code from what the visitor types (the same
parser search uses), so remembering costs no model call. Contact details are
never stored here: they go only to the Leads table, after confirmation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from . import config
from .tools import listings

SESSION_TTL_SECONDS = 24 * 3600
PROFILE_TTL_MINUTES = 90 * 24 * 60
PROFILE_KEY = "profile"
MAX_SEEN = 8

_NAME = re.compile(
    r"\b(?i:my name is|i am|i'm|i’m|this is|call me)\s+([A-Z][a-zA-Z'-]+)(?:\s+[A-Z][a-zA-Z'-]+)?"
)
_PURPOSE = (
    (
        "invest",
        re.compile(r"\b(invest(ment|or|ing)?|rental income|rent (it )?out|yield|roi)\b", re.I),
    ),
    ("live", re.compile(r"\b(live in|move in|family home|for my family|to live|relocat)", re.I)),
)
# The remembered budget is shared with the agent only when the question is about it:
# "Tell me about Chelsea Residences" must not be judged against an old budget.
_BUDGET_ASK = re.compile(
    r"\b(budget|afford\w*|price range|within my|my (price|range|limit|max)|can i (buy|get))\b",
    re.I,
)


def mentions_budget(prompt: str) -> bool:
    return bool(_BUDGET_ASK.search(prompt or ""))


def checkpointer() -> BaseCheckpointSaver:
    table = config.sessions_table()
    if not table:
        return InMemorySaver()
    from langgraph_checkpoint_aws import DynamoDBSaver

    return DynamoDBSaver(
        table_name=table, region_name=config.region(), ttl_seconds=SESSION_TTL_SECONDS
    )


def store() -> BaseStore:
    table = config.memory_table()
    if not table:
        return InMemoryStore()
    from langgraph_checkpoint_aws import DynamoDBStore

    return DynamoDBStore(
        table_name=table,
        region_name=config.region(),
        ttl={"default_ttl": PROFILE_TTL_MINUTES, "refresh_on_read": True},
    )


def namespace(visitor_id: str) -> tuple[str, str]:
    return ("visitor", visitor_id)


def update_profile(
    profile: dict[str, Any] | None, prompt: str, seen_ids: list[str] | None = None
) -> dict[str, Any]:
    """The profile after this message: newest stated preference wins, lists merge."""
    p = dict(profile or {})
    f = listings.parse_filters(prompt)

    def merge(key: str, values: list[str]) -> None:
        if values:
            p[key] = sorted({*p.get(key, []), *values})[:6]

    merge("areas", [*f.get("areas", []), *f.get("unlisted_areas", [])])
    merge("developers", f.get("developers", []))
    merge("property_types", f.get("types", []))
    for key, out in (
        ("bedrooms", "bedrooms"),
        ("min_bedrooms", "min_bedrooms"),
        ("max_price", "budget_max_aed"),
        ("min_price", "budget_min_aed"),
    ):
        if f.get(key) is not None:
            p[out] = f[key]
    if m := _NAME.search(prompt):
        p["first_name"] = m.group(1)
    for purpose, rx in _PURPOSE:
        if rx.search(prompt):
            p["purpose"] = purpose
    if seen_ids:
        p["listings_seen"] = [*dict.fromkeys([*seen_ids, *p.get("listings_seen", [])])][:MAX_SEEN]
    if p != (profile or {}):
        p["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return p


def describe(profile: dict[str, Any] | None, *, with_budget: bool = True) -> str:
    """One line for the agent's context, e.g. 'budget up to AED 3,000,000; Dubai Marina'.

    with_budget=False leaves the budget out (the question doesn't ask about it).
    """
    if not profile:
        return ""
    parts = []
    if n := profile.get("first_name"):
        parts.append(f"name {n}")
    if with_budget and (v := profile.get("budget_max_aed")):
        parts.append(f"budget up to AED {v:,}")
    if with_budget and (v := profile.get("budget_min_aed")):
        parts.append(f"budget from AED {v:,}")
    if v := profile.get("bedrooms"):
        parts.append(f"{v} bedrooms")
    if v := profile.get("min_bedrooms"):
        parts.append(f"at least {v} bedrooms")
    for key in ("areas", "developers", "property_types"):
        if profile.get(key):
            parts.append(f"{key.replace('_', ' ')}: {', '.join(profile[key])}")
    if v := profile.get("purpose"):
        parts.append("buying to invest" if v == "invest" else "buying to live in")
    if ids := profile.get("listings_seen"):
        titles = [p["title"] for i in ids[:4] if (p := listings.get(i))]
        if titles:
            parts.append(f"looked at: {'; '.join(titles)}")
    return "; ".join(parts)
