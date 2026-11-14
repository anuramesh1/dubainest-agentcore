"""POST /api/chat: screen the question, run the agent, relay its SSE events unchanged.

    AGENT_MODE=local      run agent/ in this process (local dev, step 4)
    AGENT_MODE=agentcore  InvokeAgentRuntime and pass its stream through (step 7)

guard_in runs here, before any agent starts (spec §10): the regex
prompt-injection screen and the 2,000-character cap ported from dubainest-ai
agent_service.py. The daily cap and per-IP limit arrive with step 7.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 2000
MAX_TURN_CHARS = 4000
MAX_TURNS = 12  # the UI sends its last few turns; the agent uses the last 6

REFUSAL = (
    "I can only help with Dubai real estate questions. "
    "Please ask about properties, market trends, ROI, or viewings."
)

# Ported from dubainest-ai backend/app/services/agent_service.py.
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"you\s+are\s+now\s+(?!a\s+real\s+estate)",
    r"act\s+as\s+(?!a\s+(real\s+estate|dubai|property|investment))",
    r"pretend\s+(you\s+are|to\s+be)\s+(?!a\s+(real\s+estate|dubai))",
    r"jailbreak",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"###\s*(instruction|system|prompt)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""

    @field_validator("content")
    @classmethod
    def cap(cls, v: str) -> str:
        return v[:MAX_TURN_CHARS]


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    property_id: str | None = Field(default=None, max_length=20)
    history: list[ChatTurn] = Field(default_factory=list)

    @field_validator("prompt")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("history")
    @classmethod
    def recent_only(cls, v: list[ChatTurn]) -> list[ChatTurn]:
        return v[-MAX_TURNS:]


def guard_in(prompt: str) -> tuple[str, str | None]:
    """(prompt to use, refusal or None). Long prompts are cut, not rejected."""
    clean = prompt.strip()[:MAX_PROMPT_CHARS]
    if _INJECTION_RE.search(clean):
        return clean, REFUSAL
    return clean, None


def _event(kind: str, content: Any) -> dict[str, Any]:
    return {"role": "assistant", "event_type": kind, "content": content}


async def _refuse() -> AsyncIterator[dict[str, Any]]:
    # Shown as a normal reply: the UI ignores the content of "done" events.
    yield _event("chunk", REFUSAL)
    yield _event("done", "")


async def _agent_events(request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    mode = os.getenv("AGENT_MODE", "local")
    if mode == "local":
        from agent.stream import stream_events  # imported here: the Lambda image won't ship agent/

        async for ev in stream_events(request):
            yield ev
    else:
        log.error("AGENT_MODE=%s is not available yet (AgentCore relay: build step 7)", mode)
        yield _event("error", "Chat is not available right now. Please try again later.")


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(body: ChatRequest) -> StreamingResponse:
    prompt, refusal = guard_in(body.prompt)
    if refusal:
        log.warning("guard_in blocked a prompt (len=%d)", len(body.prompt))
        events = _refuse()
    else:
        events = _agent_events(
            {
                "prompt": prompt,
                "property_id": body.property_id,
                "history": [t.model_dump() for t in body.history],
            }
        )
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
