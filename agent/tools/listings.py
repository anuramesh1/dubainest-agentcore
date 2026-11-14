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
    """The fields an agent may quote. `gross_yield_pct` 0 means no estimate (off-plan)."""
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


def search(
    filters: dict[str, Any],
    text: str = "",
    limit: int = 5,
    semantic: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Apply filters; if nothing matches, relax price then bedrooms and say so.

    With no filters at all, rank by hybrid score instead: `semantic` (vector
    similarity per listing id, from vector_index.rank) plus keyword overlap.
    """

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

    ranking = "price"
    if not any(k in filters for k in FILTER_KEYS):
        ranked = hybrid_rank(text, semantic)
        if ranked:
            rows, ranking = ranked, "hybrid" if semantic else "keyword"

    if filters.get("sort") == "yield":
        rows, ranking = sorted(rows, key=lambda p: -p["roi"]), "yield"
    out: dict[str, Any] = {
        "filters": {k: v for k, v in filters.items() if k != "sort"},
        "relaxed": relaxed,
        # 0 when filters were widened: these listings do NOT meet every criterion asked for
        "exact_matches": 0 if relaxed else len(rows),
        "ranked_by": ranking,
        "total_matches": len(rows),
        "listings": [summary(p) for p in rows[:limit]],
    }
    if relaxed:
        out["note"] = (
            "Nothing matched every criterion. These listings match after dropping: "
            + ", ".join(relaxed)
            + ". Say so, and describe each listing as it is (e.g. its real bedroom count)."
        )
    return out


_STOP = set(
    "a an the in of for to and or with me my i is are what which show find any some "
    "something anything looking look want would like need near have has get can you "
    "dubai property properties listing listings home homes buy".split()
)
KEYWORD_WEIGHT = 0.3  # tuned on a handful of descriptive queries (step 4)
MIN_HYBRID_SCORE = 0.2


def keyword_scores(text: str) -> dict[str, float]:
    """Share of the question's content words found in each listing's text."""
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}
    if not words:
        return {}
    out = {}
    for p in all_listings():
        hay = " ".join([p["title"], p["description"], " ".join(p["amenities"])]).lower()
        hits = sum(1 for w in words if re.search(rf"\b{w}s?\b", hay))
        if hits:
            out[p["id"]] = hits / len(words)
    return out


def hybrid_rank(text: str, semantic: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Vector similarity + KEYWORD_WEIGHT × keyword overlap. Keyword-only without an index."""
    kw = keyword_scores(text)
    if not semantic:
        return [get(i) for i, _ in sorted(kw.items(), key=lambda t: -t[1])]  # type: ignore[misc]
    scores = {i: semantic.get(i, 0.0) + KEYWORD_WEIGHT * kw.get(i, 0.0) for i in semantic}
    best = sorted(scores.items(), key=lambda t: -t[1])
    return [get(i) for i, sc in best if sc >= MIN_HYBRID_SCORE and get(i)]  # type: ignore[misc]


# ── Lookups used by the agents' tools ────────────────────────────────────────


def resolve(name_or_id: str | None) -> dict[str, Any] | None:
    """A listing by id ("10"), link ("/property/10") or name ("DAMAC Islands")."""
    if not name_or_id:
        return None
    key = str(name_or_id).strip().rsplit("/", 1)[-1]
    if p := get(key):
        return p
    found = mentioned_listings(str(name_or_id))
    if found:
        return found[0]
    low = str(name_or_id).lower()
    return next((p for p in all_listings() if low and low in p["title"].lower()), None)


def comparison(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Figures an agent would otherwise work out itself (and so not be grounded):
    lowest/highest across the rows, and the gaps when exactly two are compared."""
    if len(rows) < 2:
        return None
    by_price = sorted(rows, key=lambda r: r["price_aed"])
    per_sqft = [r["price_per_sqft_aed"] for r in rows if r.get("price_per_sqft_aed")]
    out: dict[str, Any] = {
        "lowest_price": {"title": by_price[0]["title"], "price_aed": by_price[0]["price_aed"]},
        "highest_price": {"title": by_price[-1]["title"], "price_aed": by_price[-1]["price_aed"]},
        "price_per_sqft_aed": (
            {"lowest": min(per_sqft), "highest": max(per_sqft)} if per_sqft else None
        ),
    }
    if len(rows) == 2:
        a, b = by_price
        out["price_difference_aed"] = b["price_aed"] - a["price_aed"]
        out["size_difference_sqft"] = abs(b["size_sqft"] - a["size_sqft"])
    return out
