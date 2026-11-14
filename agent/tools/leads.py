"""Viewing requests → the shared Leads table. Called only after the user confirms (spec §10)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .. import config


def _dynamodb_writer(item: dict[str, Any]) -> None:
    import boto3

    boto3.resource("dynamodb", region_name=config.region()).Table(config.leads_table()).put_item(
        Item=item, ConditionExpression="attribute_not_exists(leadId)"
    )


# Swapped for an in-memory list in tests and evals, so they never write to AWS.
WRITER: Callable[[dict[str, Any]], None] = _dynamodb_writer


def save_viewing_request(details: dict[str, Any]) -> str:
    lead_id = str(uuid4())
    item = {
        "leadId": lead_id,
        "propertyId": details["property_id"],
        "createdAt": datetime.now(UTC).isoformat(),
        "source": "chat_scheduler",
        "name": details["name"],
        "email": details.get("email") or "",
        "phone": details.get("phone") or "",
        "preferredTime": details["when"],
        "message": f"Viewing request from chat for {details['property_title']}",
    }
    WRITER(item)
    return lead_id
