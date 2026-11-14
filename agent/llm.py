"""The router's model call: Nova Micro with structured output, through
langchain-aws (ChatBedrockConverse). The Strands agents' model lives in
agent/specialists/agents.py. Tests pass fakes to build_graph(), so they need no AWS.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from . import config
from .state import Intent


class Route(BaseModel):
    """The router's structured output (spec §5)."""

    intent: Intent = Field(description="The single best intent for the user's question.")
    second_intent: Intent | None = Field(
        default=None,
        description="Only when the question clearly asks for two different things, "
        "e.g. listings AND their yield. Otherwise null.",
    )
    # Nova Micro occasionally omits this; a missing value is not a reason to distrust the route.
    confidence: float = Field(default=0.75, ge=0, le=1, description="How sure you are, 0 to 1.")


def _usage(msg: Any) -> dict[str, int]:
    u = getattr(msg, "usage_metadata", None) or {}
    return {
        "input_tokens": int(u.get("input_tokens", 0)),
        "output_tokens": int(u.get("output_tokens", 0)),
    }


@lru_cache(maxsize=4)
def _model(model_id: str, max_tokens: int, temperature: float) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model=model_id, region_name=config.region(), max_tokens=max_tokens, temperature=temperature
    )


async def classify(system: str, user: str) -> tuple[Route, dict[str, Any]]:
    """Router call: Nova Micro with structured output. Returns (route, trace entry)."""
    model_id = config.router_model_id()
    llm = _model(model_id, 200, 0.0).with_structured_output(Route, include_raw=True)
    t0 = time.perf_counter()
    out = await llm.ainvoke([SystemMessage(system), HumanMessage(user)])
    if out.get("parsed") is None:
        raise ValueError(f"router output not parsed: {out.get('parsing_error')}")
    return out["parsed"], {
        "model": model_id,
        "ms": round((time.perf_counter() - t0) * 1000),
        **_usage(out["raw"]),
    }
