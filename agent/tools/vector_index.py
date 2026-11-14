"""Vector search over the 20 listings without a vector database (spec §7).

scripts/build_index.py embeds each listing with Titan Text Embeddings v2 and
writes listings_index.npz (unit vectors + listing ids). The agent loads it once
per container and ranks by cosine similarity in NumPy: each question costs one
query embedding (about 50 tokens) and no database.

INDEX_URI: a local path (default data/listings_index.npz) or s3://bucket/key.
If the file is missing or Bedrock fails, callers fall back to keyword ranking.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from functools import lru_cache
from typing import Any

import numpy as np

from .. import config

log = logging.getLogger(__name__)

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 512


def index_uri() -> str:
    return os.getenv("INDEX_URI", str(config.DATA_DIR / "listings_index.npz"))


def listing_text(p: dict[str, Any]) -> str:
    """What gets embedded for a listing (build_index.py uses the same function)."""
    beds = "studio" if p["bedrooms"] == 0 else f"{p['bedrooms']}-bedroom"
    return (
        f"{p['title']}. {beds} {p['type']} by {p['developer']} in {p['area']}. "
        f"{p['description']} Amenities: {', '.join(p['amenities'])}."
    )


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.region())


def embed(text: str) -> np.ndarray:
    """One Titan v2 call → a unit vector."""
    body = json.dumps({"inputText": text[:8000], "dimensions": DIMENSIONS, "normalize": True})
    resp = _client().invoke_model(modelId=EMBED_MODEL_ID, body=body)
    vec = np.asarray(json.loads(resp["body"].read())["embedding"], dtype=np.float32)
    return vec / (np.linalg.norm(vec) or 1.0)


def _read_bytes(uri: str) -> bytes:
    if uri.startswith("s3://"):
        import boto3

        bucket, key = uri[5:].split("/", 1)
        s3 = boto3.client("s3", region_name=config.region())
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    with open(uri, "rb") as f:
        return f.read()


@lru_cache(maxsize=1)
def load() -> tuple[np.ndarray, tuple[str, ...]] | None:
    """(vectors [n, d], ids) or None when there is no index yet."""
    uri = index_uri()
    try:
        data = np.load(io.BytesIO(_read_bytes(uri)), allow_pickle=False)
    except (FileNotFoundError, OSError) as exc:
        log.warning("no vector index at %s (%s); keyword ranking only", uri, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — e.g. S3 access denied: degrade, don't fail
        log.warning("could not load vector index %s: %s", uri, exc)
        return None
    return data["vectors"], tuple(str(i) for i in data["ids"])


def rank(text: str, min_score: float = 0.15) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    """Listing ids by similarity to `text`, best first, and a trace entry.
    Empty list when there is no index or the embedding call fails."""
    idx = load()
    if idx is None:
        return [], {"vector": "no_index"}
    vectors, ids = idx
    t0 = time.perf_counter()
    try:
        q = embed(text)
    except Exception as exc:  # noqa: BLE001 — keyword ranking still works
        log.warning("query embedding failed: %s", exc)
        return [], {"vector": "embed_failed", "error": type(exc).__name__}
    scores = vectors @ q
    order = np.argsort(-scores)
    hits = [(ids[i], round(float(scores[i]), 3)) for i in order if scores[i] >= min_score]
    # "embedding", not "model": the cost eval counts chat-model calls only (spec §8
    # lists the query embedding as its own, near-zero line).
    return hits, {
        "vector": "ok",
        "embedding": EMBED_MODEL_ID,
        "ms": round((time.perf_counter() - t0) * 1000),
    }
