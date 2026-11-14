#!/usr/bin/env python3
"""Ask the deployed AgentCore Runtime a question and print its stream as it arrives.

    .venv/bin/python scripts/invoke_agent.py "2 bed apartments under 3M"
    .venv/bin/python scripts/invoke_agent.py --property 10 "What's the expected ROI?"
    .venv/bin/python scripts/invoke_agent.py --events "hi"     # raw SSE events + timings
    .venv/bin/python scripts/invoke_agent.py --session S --visitor V "..."   # memory: reuse ids

The runtime ARN comes from --arn, else AGENT_RUNTIME_ARN, else the
DubaiNestAgentCoreAgentStack output. Calls Bedrock through the runtime: about
$0.003 per question. The same InvokeAgentRuntime call the api Lambda makes in step 7.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator

import boto3


def runtime_arn(region: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName="DubaiNestAgentCoreAgentStack")["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == "AgentRuntimeArn")


def sse_lines(body) -> Iterator[str]:
    """Lines as they arrive. Not StreamingBody.iter_lines(): it reads 1 KB blocks and
    waits for each to fill, so a short answer would appear all at once at the end."""
    raw, pending = body._raw_stream, b""
    while chunk := raw.read1(65536):
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            yield line.decode("utf-8")
    if pending:
        yield pending.decode("utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("prompt")
    p.add_argument("--property", dest="property_id")
    p.add_argument("--arn", default=os.getenv("AGENT_RUNTIME_ARN"))
    p.add_argument("--session", help="chat session (≥33 chars); new one per run by default")
    p.add_argument("--visitor", default="cli-visitor", help="visitor id for long-term memory")
    p.add_argument("--events", action="store_true", help="print raw events with timings")
    a = p.parse_args()

    region = os.getenv("AWS_REGION", "us-east-1")
    arn = a.arn or runtime_arn(region)
    session = a.session or f"cli-{uuid.uuid4()}"
    payload = {
        "prompt": a.prompt,
        "property_id": a.property_id,
        "history": [],
        "session_id": session,  # conversation memory (the agent's checkpointer thread)
        "visitor_id": a.visitor,  # long-term memory
    }

    client = boto3.client("bedrock-agentcore", region_name=region)
    t0 = time.perf_counter()
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session,
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="text/event-stream",
    )
    first = None
    kinds: list[str] = []
    for line in sse_lines(resp["response"]):
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        kind = ev.get("event_type", "?")
        kinds.append(kind)
        dt = time.perf_counter() - t0
        if kind == "chunk" and first is None:
            first = dt
        if a.events:
            print(f"{dt:6.2f}s {json.dumps(ev, ensure_ascii=False)}")
        elif kind == "chunk":
            print(ev["content"], end="", flush=True)
        elif kind == "suggestions":
            print("\n\nSuggestions: " + " | ".join(ev["content"]))
        elif kind == "status" and ev["content"] != "Thinking...":
            print(f"[{ev['content']}]")
        elif kind == "error":
            print(f"\n[error] {ev['content']}")
    total = time.perf_counter() - t0
    first_s = f"{first:.1f}s" if first is not None else "n/a"
    print(
        f"\n— {len(kinds)} events, first text {first_s}, done {total:.1f}s, session {session}",
        file=sys.stderr,
    )
    return 0 if kinds and kinds[-1] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
