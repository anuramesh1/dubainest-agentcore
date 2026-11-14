#!/usr/bin/env python3
"""Build listings_index.npz: one Titan v2 vector per listing (spec §7).

    .venv/bin/python scripts/build_index.py                       # from data/mock_properties.py
    .venv/bin/python scripts/build_index.py --source dynamodb     # from the Properties table
    .venv/bin/python scripts/build_index.py --upload s3://bucket/listings_index.npz

Run it whenever listings change (it costs 20 embedding calls, a fraction of a
cent). The agent reads the file from INDEX_URI (default data/listings_index.npz,
which is gitignored); from step 6 the AgentCore container reads it from S3.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=["seed", "dynamodb"], default="seed")
    p.add_argument("--out", default=str(REPO / "data" / "listings_index.npz"))
    p.add_argument("--upload", help="also put the file at s3://bucket/key")
    a = p.parse_args()

    os.environ["LISTINGS_SOURCE"] = a.source
    from agent.tools import listings, vector_index

    rows = listings.all_listings()
    print(f"Embedding {len(rows)} listings ({a.source}) with {vector_index.EMBED_MODEL_ID}...")
    vectors = np.stack([vector_index.embed(vector_index.listing_text(r)) for r in rows])
    ids = np.array([r["id"] for r in rows])

    buf = io.BytesIO()
    np.savez_compressed(buf, vectors=vectors.astype(np.float32), ids=ids)
    Path(a.out).write_bytes(buf.getvalue())
    print(f"✓ wrote {a.out} ({vectors.shape[0]} × {vectors.shape[1]}, {len(buf.getvalue())} bytes)")

    if a.upload:
        import boto3

        bucket, key = a.upload.removeprefix("s3://").split("/", 1)
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
        print(f"✓ uploaded to {a.upload}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
