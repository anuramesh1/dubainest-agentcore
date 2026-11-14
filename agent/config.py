"""Agent settings from environment variables (read on use, so tests can override)."""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("AGENT_DATA_DIR", REPO / "data"))


def region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def router_model_id() -> str:
    """Cheap classifier (spec §13 decision 3)."""
    return os.getenv("ROUTER_MODEL_ID", "us.amazon.nova-micro-v1:0")


def writer_model_id() -> str:
    """The Strands agents' model (spec §13 decision 2). Sonnet is a config switch."""
    return os.getenv("WRITER_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


def writer_max_tokens() -> int:
    return int(os.getenv("WRITER_MAX_TOKENS", "600"))


def agent_max_turns() -> int:
    """Model calls one Strands agent may make per question (tool rounds + answer)."""
    return int(os.getenv("AGENT_MAX_TURNS", "4"))


def router_min_confidence() -> float:
    """Below this the keyword router decides instead (spec §5)."""
    return float(os.getenv("ROUTER_MIN_CONFIDENCE", "0.6"))


def listings_source() -> str:
    """`dynamodb` (default; one Scan per container) or `seed` (data/mock_properties.py, for evals/tests)."""
    return os.getenv("LISTINGS_SOURCE", "dynamodb")


def properties_table() -> str:
    return os.getenv("DYNAMODB_PROPERTIES_TABLE", "dubainest-properties-dev")


def leads_table() -> str:
    return os.getenv("DYNAMODB_LEADS_TABLE", "dubainest-leads-dev")


def sessions_table() -> str | None:
    """Conversation memory (LangGraph checkpointer). Unset: in-process memory."""
    return os.getenv("SESSIONS_TABLE") or None


def memory_table() -> str | None:
    """Long-term visitor memory (LangGraph store). Unset: in-process memory."""
    return os.getenv("MEMORY_TABLE") or None
