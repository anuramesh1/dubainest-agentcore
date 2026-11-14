"""Run the graph for one chat request and yield the UI's SSE events (spec §2).

    {"role": "assistant", "event_type": "status" | "chunk" | "suggestions" | "done" | "error",
     "content": ...}

Used in-process (AGENT_MODE=local, evals, cli) and by the AgentCore entrypoint,
so the event stream is identical in both places.

`session_id` is the checkpointer's thread: the same id continues a conversation.
`visitor_id` keys the long-term profile. Both are optional; without a session
each request starts a fresh thread seeded from the browser's history.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Any

from .graph import build_graph

log = logging.getLogger(__name__)
SAFE_ERROR = "Something went wrong. Please try again or rephrase your question."


@lru_cache(maxsize=1)
def default_graph():
    return build_graph()


def _event(kind: str, content: Any) -> dict[str, Any]:
    return {"role": "assistant", "event_type": kind, "content": content}


async def stream_events(
    request: dict[str, Any],
    graph=None,
    on_final: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """request: the body of POST /api/chat: prompt, property_id?, history?, locale?,
    session_id?, visitor_id?"""
    graph = graph or default_graph()
    session = request.get("session_id") or f"anon-{uuid.uuid4()}"
    state_in = {
        "prompt": request["prompt"],
        "property_id": request.get("property_id"),
        "visitor_id": request.get("visitor_id"),
        "locale": request.get("locale") or "en",
        "request_history": [
            {"role": t.get("role", ""), "content": t.get("content", "")}
            for t in (request.get("history") or [])
        ],
        # this turn's fields start empty; history / pending viewing come from the checkpoint
        "facts": {},
        "trace": None,
        "answer": "",
        "suggestions": [],
        "seen_ids": [],
        "intents": [],
        "route": "",
    }
    run_config = {"configurable": {"thread_id": session}}
    yield _event("status", "Thinking...")
    final: dict[str, Any] = {}
    try:
        async for mode, payload in graph.astream(
            state_in,
            run_config,
            stream_mode=["custom", "values"],
            durability="exit",  # one checkpoint write per question, not one per node
        ):
            if mode == "custom":
                yield _event(payload["event_type"], payload["content"])
            else:
                final = payload
    except Exception:
        log.exception("agent failed for prompt: %.100s", request.get("prompt", ""))
        yield _event("error", SAFE_ERROR)
        return
    finally:
        if on_final:
            on_final(final)
    yield _event("done", "")
