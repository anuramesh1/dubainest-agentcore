"""The 20 listings, loaded once per container, plus prompt → filter parsing.

Source (LISTINGS_SOURCE): `dynamodb` reads the shared Properties table with one
Scan at first use (spec §7); `seed` reads data/mock_properties.py (evals, tests).
"""

from __future__ import annotations

import importlib.util
import re
from decimal import Decimal
from functools import lru_cache
from typing import Any

from .. import config

# ── Loading ───────────────────────────────────────────────────────────────────


def _from_item(item: dict[str, Any]) -> dict[str, Any]:
    d = dict(item)
    if "propertyId" in d:
        d["id"] = d.pop("propertyId")
    if "priceAed" in d:
        d["price"] = d.pop("priceAed")
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = float(v) if k in ("roi", "lat", "lng") else int(v)
    return d


def _load_seed() -> list[dict[str, Any]]:
    path = config.DATA_DIR / "mock_properties.py"
    spec = importlib.util.spec_from_file_location("dubainest_seed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return [dict(p) for p in mod.PROPERTIES]


def _load_dynamodb() -> list[dict[str, Any]]:
    import boto3

    table = boto3.resource("dynamodb", region_name=config.region()).Table(config.properties_table())
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return [_from_item(i) for i in items]
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


@lru_cache(maxsize=1)
def all_listings() -> tuple[dict[str, Any], ...]:
    rows = _load_seed() if config.listings_source() == "seed" else _load_dynamodb()
    return tuple(sorted(rows, key=lambda p: (p["price"], p["id"])))


def get(property_id: str | None) -> dict[str, Any] | None:
    if not property_id:
        return None
    return next((p for p in all_listings() if p["id"] == property_id), None)


def project_name(p: dict[str, Any]) -> str:
    """'Burj Vista — Downtown Dubai' → 'Burj Vista'."""
    return p["title"].split(" — ")[0].strip()


GOLDEN_VISA_AED = 2_000_000  # also in area_stats.json overview


def summary(p: dict[str, Any]) -> dict[str, Any]:
    """The fields the writer may quote. `gross_yield_pct` 0 means no estimate (off-plan)."""
    off_plan = p["completionYear"] > 2026 or not p["roi"]
    return {
        "id": p["id"],
        "title": p["title"],
        "developer": p["developer"],
        "area": p["area"],
        "type": p["type"],
        "bedrooms": p["bedrooms"],
        "bathrooms": p["bathrooms"],
        "size_sqft": p["sizeSqft"],
        "price_aed": p["price"],
        "price_per_sqft_aed": round(p["price"] / p["sizeSqft"]) if p["sizeSqft"] else None,
        "gross_yield_pct": p["roi"],
        "completion_year": p["completionYear"],
        "status": "off-plan" if off_plan else "ready",
        "golden_visa_eligible": p["price"] >= GOLDEN_VISA_AED,
        "below_golden_visa_by_aed": max(0, GOLDEN_VISA_AED - p["price"]) or None,
        "amenities": p["amenities"][:4],
        "link": f"/property/{p['id']}",
    }


# ── Prompt parsing ────────────────────────────────────────────────────────────

AREA_ALIASES = {
    "downtown": "Downtown Dubai",
    "business bay": "Business Bay",
    "palm": "Palm Jumeirah",
    "maritime city": "Dubai Maritime City",
    "mbr city": "MBR City",
    "mbr": "MBR City",
    "mohammed bin rashid": "MBR City",
    "creek harbour": "Dubai Creek Harbour",
    "creek harbor": "Dubai Creek Harbour",
    "dubai hills": "Dubai Hills Estate",
    "dubailand": "Dubailand",
    "dubai land": "Dubailand",
}
# Areas people ask about that have no listings in the catalogue.
UNLISTED_AREAS = {
    "dubai marina": "Dubai Marina",
    "marina": "Dubai Marina",
    "jvc": "JVC",
    "jumeirah village": "JVC",
    "meydan": "Meydan",
    "bluewaters": "Bluewaters Island",
}
DEVELOPERS = {"damac": "DAMAC", "emaar": "Emaar", "nakheel": "Nakheel"}
TYPES = {
    "studio": "studio",
    "apartment": "apartment",
    "flat": "apartment",
    "villa": "villa",
    "townhouse": "townhouse",
    "town house": "townhouse",
    "penthouse": "penthouse",
}

_AMOUNT = r"(?:aed\s*)?(\d+(?:[.,]\d+)*)\s*(m\b|mn\b|million|k\b|thousand)?(?:\s*aed)?"
_MAX_WORDS = (
    r"(?:under|below|less than|up to|upto|max(?:imum)?|budget(?: of| is)?|within|no more than)"
)
_MIN_WORDS = r"(?:over|above|more than|at least|minimum|min|from|starting at)"
# "more than 2 bedrooms", "at least 5% yield", "over 1,000 sqft" are not prices.
_NOT_A_PRICE = r"(?!\s*(?:-|\s)?(?:br|bed|bhk|bath|%|percent|sq|year|yr))"


def parse_amount(number: str, unit: str | None) -> int | None:
    unit = (unit or "").lower()
    raw = number.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if unit in ("m", "mn", "million"):
        value *= 1_000_000
    elif unit in ("k", "thousand"):
        value *= 1_000
    elif value < 100:  # "under 3" with no unit: read as millions
        value *= 1_000_000
    elif value < 100_000:  # a year, a size or a count, not a price
        return None
    return int(value)


def mentioned_listings(text: str) -> list[dict[str, Any]]:
    """Listings named in the text, e.g. 'Burj Vista' or 'DAMAC Islands'."""
    low = text.lower()
    found = [p for p in all_listings() if project_name(p).lower() in low]
    return found


def parse_filters(text: str) -> dict[str, Any]:
    low = " " + text.lower() + " "
    f: dict[str, Any] = {}

    areas = sorted({v for k, v in AREA_ALIASES.items() if re.search(rf"\b{k}\b", low)})
    if areas:
        f["areas"] = areas
    unlisted = sorted({v for k, v in UNLISTED_AREAS.items() if re.search(rf"\b{k}\b", low)})
    if unlisted:
        f["unlisted_areas"] = unlisted
    devs = sorted({v for k, v in DEVELOPERS.items() if k in low})
    if devs:
        f["developers"] = devs
    types = sorted({v for k, v in TYPES.items() if re.search(rf"\b{k}s?\b", low)})
    if types:
        f["types"] = types

    beds = r"(?:-|\s)?(?:br|bed(?:room)?s?|bhk)\b"
    if m := re.search(rf"\b(?:more than|over|above)\s+(\d)\s*{beds}", low):
        f["min_bedrooms"] = int(m.group(1)) + 1
    elif m := re.search(rf"\b(?:at least|minimum|min)\s+(\d)\s*{beds}|\b(\d)\s*\+\s*{beds}", low):
        f["min_bedrooms"] = int(m.group(1) or m.group(2))
    elif m := re.search(rf"\b(\d)\s*{beds}", low):
        f["bedrooms"] = int(m.group(1))
    elif "studio" in low:
        f["bedrooms"] = 0

    between = re.search(rf"between\s+{_AMOUNT}\s+(?:and|-|to)\s+{_AMOUNT}", low)
    if between:
        lo = parse_amount(between.group(1), between.group(2) or between.group(4))
        hi = parse_amount(between.group(3), between.group(4))
        if lo and hi:
            f["min_price"], f["max_price"] = min(lo, hi), max(lo, hi)
    else:
        for words, key in ((_MAX_WORDS, "max_price"), (_MIN_WORDS, "min_price")):
            m = re.search(rf"{words}\s+{_AMOUNT}{_NOT_A_PRICE}", low)
            amount = parse_amount(m.group(1), m.group(2)) if m else None
            if amount:
                f[key] = amount

    if re.search(r"\b(yield|roi|return|rental income|cash ?flow)\b", low):
        f["sort"] = "yield"
    if re.search(r"\boff[- ]?plan\b", low):
        f["status"] = "off-plan"
    elif re.search(r"\b(ready|completed|move[- ]in)\b", low):
        f["status"] = "ready"
    return f


FILTER_KEYS = (
    "areas",
    "developers",
    "types",
    "bedrooms",
    "min_bedrooms",
    "min_price",
    "max_price",
    "status",
)


def search(filters: dict[str, Any], text: str = "", limit: int = 5) -> dict[str, Any]:
    """Apply filters; if nothing matches, relax price then bedrooms and say so."""

    def match(p: dict[str, Any], f: dict[str, Any]) -> bool:
        s = summary(p)
        return (
            (not f.get("areas") or p["area"] in f["areas"])
            and (not f.get("developers") or p["developer"] in f["developers"])
            and (not f.get("types") or p["type"] in f["types"])
            and (f.get("bedrooms") is None or p["bedrooms"] == f["bedrooms"])
            and (f.get("min_bedrooms") is None or p["bedrooms"] >= f["min_bedrooms"])
            and (not f.get("min_price") or p["price"] >= f["min_price"])
            and (not f.get("max_price") or p["price"] <= f["max_price"])
            and (not f.get("status") or s["status"] == f["status"])
        )

    relaxed: list[str] = []
    active = dict(filters)
    rows = [p for p in all_listings() if match(p, active)]
    for key in (("min_price", "max_price"), ("bedrooms", "min_bedrooms"), ("types",), ("status",)):
        if rows:
            break
        if any(active.get(k) is not None for k in key):
            for k in key:
                active.pop(k, None)
            relaxed.extend(key)
            rows = [p for p in all_listings() if match(p, active)]

    if not any(k in filters for k in FILTER_KEYS):
        rows = _keyword_rank(text) or rows

    if filters.get("sort") == "yield":
        rows = sorted(rows, key=lambda p: -p["roi"])
    return {
        "filters": {k: v for k, v in filters.items() if k != "sort"},
        "relaxed": relaxed,
        "total_matches": len(rows),
        "listings": [summary(p) for p in rows[:limit]],
    }


_STOP = set(
    "a an the in of for to and or with me my i is are what which show find any some "
    "dubai property properties listing listings home homes buy".split()
)


def _keyword_rank(text: str) -> list[dict[str, Any]]:
    """Fallback when the prompt has no filters: word overlap with title/description/amenities.
    Step 4 adds the Titan vector index in front of this."""
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}
    if not words:
        return []
    scored = []
    for p in all_listings():
        hay = " ".join([p["title"], p["description"], " ".join(p["amenities"])]).lower()
        score = sum(1 for w in words if re.search(rf"\b{w}s?\b", hay))
        if score:
            scored.append((score, p))
    scored.sort(key=lambda t: (-t[0], t[1]["price"]))
    return [p for _, p in scored]
