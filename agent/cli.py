"""Ask the graph a question from the terminal, in-process (AGENT_MODE=local).

    .venv/bin/python -m agent.cli "2BR in Business Bay under 3M"
    .venv/bin/python -m agent.cli --property 10 "What's the expected ROI?"
    .venv/bin/python -m agent.cli --trace "What's the yield on Burj Vista?"

Uses your AWS credentials for Bedrock. Listings come from the Properties table
unless LISTINGS_SOURCE=seed. Prints the stream as the UI would receive it.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from .stream import stream_events


async def main() -> None:
    p = argparse.ArgumentParser(description="Run one question through the DubaiNest graph.")
    p.add_argument("prompt")
    p.add_argument("--property", dest="property_id", help="listing open in the UI, e.g. 10")
    p.add_argument("--history", help="JSON list of {role, content} turns")
    p.add_argument("--trace", action="store_true", help="print the node/model trace at the end")
    p.add_argument("--events", action="store_true", help="print raw SSE events instead of text")
    a = p.parse_args()

    final: dict = {}
    request = {"prompt": a.prompt, "property_id": a.property_id,
               "history": json.loads(a.history) if a.history else []}
    async for ev in stream_events(request, on_final=final.update):
        if a.events:
            print(json.dumps(ev, ensure_ascii=False))
        elif ev["event_type"] == "chunk":
            print(ev["content"], end="", flush=True)
        elif ev["event_type"] == "suggestions":
            print("\n\nSuggestions:", *[f"  - {s}" for s in ev["content"]], sep="\n")
        elif ev["event_type"] == "error":
            print(f"\n[error] {ev['content']}")
    print()
    if a.trace:
        print(json.dumps(final.get("trace", []), indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
