"""Talk to the graph from the terminal, in-process (AGENT_MODE=local).

    .venv/bin/python -m agent.cli "2BR in Business Bay under 3M"
    .venv/bin/python -m agent.cli --property 10 --trace "What's the expected ROI?"
    .venv/bin/python -m agent.cli --chat                        # a conversation (memory on)
    .venv/bin/python -m agent.cli --chat --visitor me           # same visitor across runs*

Uses your AWS credentials for Bedrock. Listings come from the Properties table
unless LISTINGS_SOURCE=seed. Memory is in-process unless SESSIONS_TABLE /
MEMORY_TABLE are set (*so a profile survives between runs only with MEMORY_TABLE).
In --chat, type /new for a new session (same visitor) and /quit to leave.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from .stream import default_graph, stream_events


async def ask(request: dict[str, Any], events: bool) -> dict[str, Any]:
    final: dict[str, Any] = {}
    async for ev in stream_events(request, graph=default_graph(), on_final=final.update):
        kind = ev["event_type"]
        if events:
            print(json.dumps(ev, ensure_ascii=False))
        elif kind == "chunk":
            print(ev["content"], end="", flush=True)
        elif kind == "status" and ev["content"] != "Thinking...":
            print(f"[{ev['content']}]")
        elif kind == "suggestions":
            print("\n\nSuggestions:", *[f"  - {s}" for s in ev["content"]], sep="\n")
        elif kind == "error":
            print(f"\n[error] {ev['content']}")
    print()
    return final


async def main() -> None:
    p = argparse.ArgumentParser(description="Run questions through the DubaiNest graph.")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--property", dest="property_id", help="listing open in the UI, e.g. 10")
    p.add_argument("--history", help="JSON list of {role, content} turns (as the browser sends)")
    p.add_argument("--session", help="chat session id (conversation memory)")
    p.add_argument("--visitor", help="visitor id (long-term memory)")
    p.add_argument("--chat", action="store_true", help="interactive conversation")
    p.add_argument("--trace", action="store_true", help="print the node/model trace")
    p.add_argument("--events", action="store_true", help="print raw SSE events")
    a = p.parse_args()

    session = a.session or f"cli-{uuid.uuid4()}"
    visitor = a.visitor or f"cli-visitor-{uuid.uuid4().hex[:8]}"
    base = {"property_id": a.property_id, "session_id": session, "visitor_id": visitor}

    if not a.chat:
        if not a.prompt:
            p.error("a question, or --chat")
        hist = json.loads(a.history) if a.history else []
        final = await ask({**base, "prompt": a.prompt, "history": hist}, a.events)
        if a.trace:
            print(json.dumps(final.get("trace", []), indent=1, default=str))
        return

    print(f"session {session}  visitor {visitor}   (/new, /quit)")
    while True:
        try:
            q = input("\nyou › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q in ("/quit", "/exit"):
            break
        if q == "/new":
            base["session_id"] = f"cli-{uuid.uuid4()}"
            print(f"new session {base['session_id']} (same visitor: profile kept)")
            continue
        if q:
            print("ai  › ", end="")
            final = await ask({**base, "prompt": q}, a.events)
            if a.trace:
                print(json.dumps(final.get("trace", []), indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
