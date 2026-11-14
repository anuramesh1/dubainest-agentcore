"""The AgentCore Runtime entrypoint (spec §4, build step 6).

BedrockAgentCoreApp serves the two routes AgentCore calls on port 8080:
POST /invocations (this entrypoint) and GET /ping. The entrypoint is an async
generator, so the SDK streams each yielded dict as one `data: {json}` SSE line:
exactly the UI's event contract (agent/stream.py), which the api Lambda will
relay unchanged in step 7.

    .venv/bin/python -m agent.main                 # serve locally on :8080
    curl -sN localhost:8080/invocations -H 'content-type: application/json' \\
         -d '{"prompt": "2 bed apartments under 3M"}'

Payload: the body of POST /api/chat after guard_in — prompt, property_id?,
history?, locale?. Only the api Lambda's role may invoke this runtime.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .stream import stream_events

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

BAD_REQUEST = "Please type a question about Dubai properties."


def _error(message: str) -> dict[str, Any]:
    return {"role": "assistant", "event_type": "error", "content": message}


@app.entrypoint
async def invoke(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        log.warning("invocation without a prompt")
        yield _error(BAD_REQUEST)
        return
    async for event in stream_events(payload):
        yield event


if __name__ == "__main__":
    app.run()
