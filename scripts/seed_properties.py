#!/usr/bin/env python3
"""Seed the DynamoDB properties table from the catalogue in data/mock_properties.py.

Idempotent: uses PutItem (full overwrite) keyed by propertyId, so re-running
refreshes existing items rather than duplicating them.

    python3 scripts/seed_properties.py [--table dubainest-properties-dev] [--region us-east-1]

Requires AWS credentials with dynamodb:PutItem on the target table.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "data"))

from mock_properties import PROPERTIES  # noqa: E402

_FLOAT_FIELDS = {"roi", "lat", "lng"}


def property_to_item(data: dict[str, Any]) -> dict[str, Any]:
    """Map a Property-shaped dict to a DynamoDB item (the inverse of api/properties.py)."""
    item = dict(data)
    item["propertyId"] = item.pop("id")
    item["priceAed"] = item.pop("price")
    for key in _FLOAT_FIELDS:
        if key in item and item[key] is not None:
            item[key] = Decimal(str(item[key]))
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DynamoDB properties table")
    parser.add_argument("--table", default="dubainest-properties-dev")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    print(f"Seeding {len(PROPERTIES)} properties into {args.table} ({args.region})...")
    with table.batch_writer(overwrite_by_pkeys=["propertyId"]) as batch:
        for prop in PROPERTIES:
            batch.put_item(Item=property_to_item(prop))

    # Verify count
    count = table.scan(Select="COUNT")["Count"]
    print(f"✓ Done. Table item count: {count}")


if __name__ == "__main__":
    main()
