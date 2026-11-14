"""Settings from environment variables. Read on each call so tests can override them."""

from __future__ import annotations

import os


def region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def properties_table() -> str:
    return os.getenv("DYNAMODB_PROPERTIES_TABLE", "dubainest-properties-dev")


def leads_table() -> str:
    return os.getenv("DYNAMODB_LEADS_TABLE", "dubainest-leads-dev")


def media_base_url() -> str | None:
    """CloudFront base for listing photos, e.g. https://<assets-domain>/media/photos."""
    return os.getenv("MEDIA_BASE_URL") or None


def cache_seconds() -> float:
    """How long one instance reuses its scan of the Properties table."""
    return float(os.getenv("PROPERTIES_CACHE_SECONDS", "60"))


def allowed_origins() -> list[str]:
    """CORS origins for local dev. In AWS the UI and /api/* share one CloudFront domain."""
    raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:4200")
    return [o.strip() for o in raw.split(",") if o.strip()]
