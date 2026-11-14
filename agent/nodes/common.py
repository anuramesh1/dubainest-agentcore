"""Helpers shared by nodes: history access and the text a node should look at."""
from __future__ import annotations

import re
from typing import Any

from ..state import AgentState

HISTORY_TURNS = 6
_FOLLOW_UP = re.compile(r"^\s*(and|what about|how about|also|ok,? and|same for|in)\b", re.I)
AFFIRMATIVE = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm(ed)?|sure|ok(ay)?|please do|go ahead|book it|do it|correct)\b",
    re.I,
)


def history(state: AgentState) -> list[dict[str, Any]]:
    return [t for t in (state.get("history") or []) if (t.get("content") or "").strip()][
        -HISTORY_TURNS:
    ]


def last(state: AgentState, role: str) -> str:
    for t in reversed(history(state)):
        if t.get("role") == role:
            return t["content"]
    return ""


def is_follow_up(prompt: str) -> bool:
    return bool(_FOLLOW_UP.match(prompt)) or len(prompt.split()) <= 4


def context_text(state: AgentState) -> str:
    """The prompt, plus the previous user turn when the prompt is a short follow-up
    ("and in Palm?"), so filters and names carry over."""
    prompt = state["prompt"]
    prev = last(state, "user")
    return f"{prev}\n{prompt}" if prev and is_follow_up(prompt) else prompt


def transcript(state: AgentState) -> str:
    lines = []
    for t in history(state):
        who = "User" if t.get("role") == "user" else "DubaiNest AI"
        lines.append(f"{who}: {t['content'].strip()[:600]}")
    return "\n".join(lines)
