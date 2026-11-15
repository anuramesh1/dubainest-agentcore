"""POST /api/chat: screen the question, check the caps, run the agent, relay its
SSE events unchanged.

    AGENT_MODE=local      run agent/ in this process (local dev)
    AGENT_MODE=agentcore  InvokeAgentRuntime and pass its stream through (AWS)

guard_in runs here, before any agent starts: the regex prompt-injection screen
and the 2,000-character cap, then the per-IP and daily caps (api/limits.py).
Blocked questions never wake AgentCore.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from . import limits, settings

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 2000
MAX_TURN_CHARS = 4000
MAX_TURNS = 12  # the UI sends its last few turns; the agent uses the last 6

# Sent for a screened-out prompt, and for every message while a visitor is in the
# injection cooldown (api/limits.py). Calm and non-accusatory on purpose: the
# screen has false positives, and it says nothing about what was detected.
REFUSAL = (
    "I'm DubaiNest's property assistant, so I'll stay in that role. "
    "Ask me about listings, areas, rental yields or booking a viewing."
)

# A cheap first screen for the obvious override attempts. It is not the defence:
# paraphrases go on to the router (role changes are off_topic) and the agents'
# own rules (never reveal the instructions). Roles a buyer may reasonably ask for
# ("act as my buyer's agent") are allowed through.
_OK_ROLE = (
    r"(?!(a|an|my|the)\s+(real[\s-]*estate|dubai|property|investment|buyer|broker"
    r"|agent|advisor|adviser|consultant|mortgage|realtor))"
)
_INJECTION_PATTERNS = [
    r"(ignore|disregard|forget|erase|reset|override|clear|delete|drop)\s+"
    r"(all\s+|any\s+)?(of\s+)?(the\s+|your\s+|those\s+|these\s+)?"
    r"(previous|prior|above|earlier|initial|original|system|existing)?\s*"
    r"(instructions?|prompts?|rules|guidelines)",
    r"you\s+are\s+now\s+" + _OK_ROLE,
    r"from\s+now\s+on,?\s+(you\s+(are|will|must|should)|act|behave|respond|answer)",
    r"act\s+as\s+" + _OK_ROLE,
    r"pretend\s+(you\s+are|to\s+be|you're)\s+" + _OK_ROLE,
    r"(assume|take\s+on|adopt)\s+the\s+(role|persona|identity)\s+of\s+" + _OK_ROLE,
    r"(reveal|show|print|repeat|output|display|leak|dump|share)\s+(me\s+)?(your\s+|the\s+)?"
    r"(system\s+|hidden\s+|initial\s+|original\s+)?(prompt|instructions)",
    r"system\s+prompt",
    r"(developer|god|unrestricted|unfiltered)\s+mode",
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


# Browser-made ids (crypto.randomUUID()): letters, digits and dashes only.
_ID = r"^[A-Za-z0-9-]{8,64}$"


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    property_id: str | None = Field(default=None, max_length=20)
    history: list[ChatTurn] = Field(default_factory=list)
    # Conversation memory (one per chat window) and long-term memory (one per browser).
    session_id: str | None = Field(default=None, pattern=_ID)
    visitor_id: str | None = Field(default=None, pattern=_ID)

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


UNAVAILABLE = "Chat is not available right now. Please try again later."


def _sse_line(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


async def _reply(text: str) -> AsyncIterator[str]:
    # Shown as a normal answer: the UI ignores the content of "done" events.
    yield _sse_line(_event("chunk", text))
    yield _sse_line(_event("done", ""))


async def _local(request: dict[str, Any]) -> AsyncIterator[str]:
    from agent.stream import stream_events  # imported here: the Lambda image doesn't ship agent/

    async for ev in stream_events(request):
        yield _sse_line(ev)


# ── AgentCore relay ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _agentcore():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=settings.region(),
        # a cold microVM can take ~10 s before the first byte; an answer ~5 s more
        config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 2}),
    )


def runtime_session_id(session_id: str | None) -> str:
    """AgentCore wants ≥ 33 characters; one runtime session per chat window."""
    sid = f"dubainest-{session_id or uuid.uuid4()}"
    if len(sid) < 33:
        sid += "-" + hashlib.sha256(sid.encode()).hexdigest()[: 33 - len(sid)]
    return sid[:100]


async def _agentcore_stream(request: dict[str, Any]) -> AsyncIterator[str]:
    """Pass AgentCore's SSE bytes through as they arrive. The agent already sends
    the UI's exact events (agent/main.py), so nothing is parsed or rebuilt here.

    Read with read1(), not StreamingBody.iter_lines(): iter_lines waits for 1 KB
    blocks, which would deliver a short answer all at once at the end."""
    arn = os.environ["AGENT_RUNTIME_ARN"]
    sent_any = False
    try:
        resp = await asyncio.to_thread(
            _agentcore().invoke_agent_runtime,
            agentRuntimeArn=arn,
            runtimeSessionId=runtime_session_id(request.get("session_id")),
            payload=json.dumps(request).encode(),
            contentType="application/json",
            accept="text/event-stream",
        )
        raw = resp["response"]._raw_stream
        decoder = codecs.getincrementaldecoder("utf-8")()
        while chunk := await asyncio.to_thread(raw.read1, 65536):
            text = decoder.decode(chunk)
            if text:
                sent_any = True
                yield text
        if tail := decoder.decode(b"", final=True):
            yield tail
    except Exception as exc:  # noqa: BLE001 — the browser gets a safe message, never a trace
        log.error(
            "AgentCore relay failed after %s: %s", "partial stream" if sent_any else "no data", exc
        )
        yield ("\n\n" if sent_any else "") + _sse_line(_event("error", UNAVAILABLE))


def _agent_stream(request: dict[str, Any]) -> AsyncIterator[str]:
    mode = os.getenv("AGENT_MODE", "local")
    if mode == "local":
        return _local(request)
    if mode == "agentcore" and os.getenv("AGENT_RUNTIME_ARN"):
        return _agentcore_stream(request)
    log.error(
        "chat unavailable: AGENT_MODE=%s, AGENT_RUNTIME_ARN set=%s",
        mode,
        bool(os.getenv("AGENT_RUNTIME_ARN")),
    )
    return _error_stream()


async def _error_stream() -> AsyncIterator[str]:
    yield _sse_line(_event("error", UNAVAILABLE))


def client_ip(request: Request) -> str | None:
    """The visitor's IP. Behind CloudFront the connection comes from CloudFront, so
    use CloudFront-Viewer-Address ("ip:port"), which CloudFront sets itself. Never
    X-Forwarded-For: a client can put any address in it."""
    if addr := request.headers.get("cloudfront-viewer-address"):
        return addr.rsplit(":", 1)[0].strip("[]")
    return request.client.host if request.client else None


@router.post("")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    ip = client_ip(request)
    prompt, refusal = guard_in(body.prompt)
    if refusal:
        log.warning("guard_in blocked a prompt (len=%d)", len(body.prompt))
        await asyncio.to_thread(limits.note_injection, ip)
        stream = _reply(refusal)
    elif await asyncio.to_thread(limits.cooling_down, ip):
        # Repeated attempts: the same calm reply for everything, no agent, no spend.
        log.warning("visitor in injection cooldown; replied without the agent")
        stream = _reply(REFUSAL)
    elif capped := await asyncio.to_thread(limits.check, ip):
        stream = _reply(capped)
    else:
        stream = _agent_stream(
            {
                "prompt": prompt,
                "property_id": body.property_id,
                "history": [t.model_dump() for t in body.history],
                "session_id": body.session_id,
                "visitor_id": body.visitor_id,
            }
        )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
