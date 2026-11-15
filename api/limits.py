"""Spending caps for the public chat endpoint (spec §8), checked before AgentCore starts.

    per visitor IP   PER_IP_PER_MINUTE questions a minute (default 20)
    whole site       DAILY_QUESTION_CAP questions a day, UTC (default 200; ≈ $1.30/day worst case)
    injection        INJECTION_ATTEMPTS screened-out prompts per 10 minutes (default 5);
                     after that, every message from that IP gets the calm refusal
                     with no agent call until the window ends

All are atomic DynamoDB counters (conditional ADD), so they hold across every
Lambda instance; items expire by TTL. IPs are stored only as a salted hash.
Without LIMITS_TABLE (local dev) there are no caps. If DynamoDB itself fails,
the question is allowed: reserved concurrency (3) still bounds spend.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import UTC, datetime
from functools import lru_cache

from . import settings

log = logging.getLogger(__name__)

DAILY_LIMIT_REPLY = (
    "DubaiNest AI has answered its maximum number of questions for today. "
    "Please come back tomorrow, or browse the listings and send an enquiry in the meantime."
)
RATE_LIMIT_REPLY = "You're asking faster than I can keep up. Please wait a minute and try again."


def table_name() -> str | None:
    return os.getenv("LIMITS_TABLE") or None


def daily_cap() -> int:
    return int(os.getenv("DAILY_QUESTION_CAP", "200"))


def per_ip_per_minute() -> int:
    return int(os.getenv("PER_IP_PER_MINUTE", "20"))


def injection_attempts() -> int:
    return int(os.getenv("INJECTION_ATTEMPTS", "5"))


INJECTION_WINDOW_MINUTES = 10


@lru_cache(maxsize=1)
def _table():
    import boto3

    return boto3.resource("dynamodb", region_name=settings.region()).Table(table_name())


def _bump(key: str, limit: int, ttl_seconds: int) -> bool:
    """Add 1 to `key` unless it already reached `limit`. True = allowed."""
    from botocore.exceptions import ClientError

    try:
        _table().update_item(
            Key={"pk": key},
            UpdateExpression="ADD n :one SET expires_at = if_not_exists(expires_at, :exp)",
            ConditionExpression="attribute_not_exists(n) OR n < :limit",
            ExpressionAttributeValues={
                ":one": 1,
                ":limit": limit,
                ":exp": int(time.time()) + ttl_seconds,
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        log.warning("limits table error, allowing the question: %s", exc)
        return True


def ip_key(ip: str) -> str:
    salt = os.getenv("LIMITS_SALT", "dubainest")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:20]


def check(ip: str | None) -> str | None:
    """None if this question may go ahead; otherwise the polite reply to send."""
    if not table_name():
        return None
    now = datetime.now(UTC)
    if ip and not _bump(f"ip#{ip_key(ip)}#{now:%Y%m%d%H%M}", per_ip_per_minute(), 600):
        log.info("per-IP limit reached")
        return RATE_LIMIT_REPLY
    if not _bump(f"day#{now:%Y-%m-%d}", daily_cap(), 3 * 86400):
        log.warning("daily question cap reached (%d)", daily_cap())
        return DAILY_LIMIT_REPLY
    return None


# ── Injection cooldown ───────────────────────────────────────────────────────


def _injection_key(ip: str, now: datetime) -> str:
    window = now.minute // INJECTION_WINDOW_MINUTES
    return f"inject#{ip_key(ip)}#{now:%Y%m%d%H}{window}"


def note_injection(ip: str | None) -> None:
    """Count one screened-out prompt for this visitor in the current window."""
    if table_name() and ip:
        # the limit is never reached here: cooling_down() reads the count instead
        _bump(_injection_key(ip, datetime.now(UTC)), 1_000_000, 2 * INJECTION_WINDOW_MINUTES * 60)


def cooling_down(ip: str | None) -> bool:
    """True once this visitor sent INJECTION_ATTEMPTS screened-out prompts in the
    current window. The caller then answers every message with the refusal and
    never starts an agent."""
    if not table_name() or not ip:
        return False
    try:
        item = _table().get_item(Key={"pk": _injection_key(ip, datetime.now(UTC))}).get("Item")
    except Exception as exc:  # noqa: BLE001 — never block a visitor because of a read error
        log.warning("limits table read error, no cooldown applied: %s", exc)
        return False
    return bool(item) and int(item.get("n", 0)) >= injection_attempts()
