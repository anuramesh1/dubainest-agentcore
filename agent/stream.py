"""Run the graph for one chat request and yield the UI's SSE events (spec §2).

    {"role": "assistant", "event_type": "status" | "chunk" | "suggestions" | "done" | "error",
     "content": ...}

Used in-process now (AGENT_MODE=local, evals) and by the AgentCore entrypoint
from step 6, so the event stream is identical in both places.
"""
from __future__ import annotations

import logging
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
    """request: {"prompt", "property_id"?, "history"?, "locale"?} — the body of POST /api/chat."""
    graph = graph or default_graph()
    state_in = {
        "prompt": request["prompt"],
        "property_id": request.get("property_id"),
        "history": [{"role": t.get("role", ""), "content": t.get("content", "")}
                    for t in (request.get("history") or [])],
        "locale": request.get("locale") or "en",
        "facts": {},
        "trace": [],
    }
    yield _event("status", "Thinking...")
    final: dict[str, Any] = {}
    try:
        async for mode, payload in graph.astream(state_in, stream_mode=["custom", "values"]):
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
