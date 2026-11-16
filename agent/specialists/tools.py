"""The Strands tools, built per request (spec §5).

Each tool is plain Python wrapped with Strands' @tool. ToolBox records every
result in `facts` (the output gate checks the answer against them), notes the
listings looked at (for long-term memory) and holds a viewing request until the
user says yes. Tools are read-only: request_viewing never writes a lead.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from strands import tool

from ..prompts import CONFIRM_PHRASE
from ..tools import area_stats, listings, roi, vector_index

STATUS = {
    "search_listings": "Searching listings…",
    "get_listing": "Looking up the listing…",
    "compare_listings": "Comparing listings…",
    "area_market": "Checking the market notes…",
    "calculate_roi": "Calculating ROI…",
    "request_viewing": "Preparing your viewing request…",
    "catalogue_overview": "Checking the catalogue…",
}

# Which tools each specialist gets. Two intents → the union (spec §5).
TOOLS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "property_search": ("search_listings", "get_listing", "compare_listings"),
    "market_intel": ("area_market", "get_listing"),
    "roi": ("calculate_roi", "search_listings", "get_listing"),
    "scheduler": ("request_viewing", "get_listing"),
    "general": ("catalogue_overview", "get_listing"),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class ToolBox:
    """Tools for one request, bound to what the user actually said."""

    def __init__(
        self, user_text: str, open_listing: dict[str, Any] | None = None, question: str = ""
    ):
        self.user_text = user_text  # the visitor's own words: this turn + earlier turns
        self.question = question  # this turn's question only, for search ranking
        self.open_listing = open_listing
        self.facts: dict[str, list[Any]] = {}
        self.seen_ids: list[str] = []
        self.pending_viewing: dict[str, Any] | None = None
        self.calls: list[str] = []
        self.timeline: list[dict[str, Any]] = []  # one entry per call, for the UI trace

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _record(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        self.facts.setdefault(name, []).append(result)
        return result

    def _timed(self, fn: Callable) -> Callable:
        """Note how long a tool took and how it ended. Counts and flags only: the
        trace is shown to visitors, so no arguments or results."""

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            entry: dict[str, Any] = {"name": fn.__name__, "status": "error", "summary": {}}
            try:
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    entry["status"] = "error" if "error" in result else "ok"
                    if isinstance(result.get("listings"), list):
                        entry["summary"]["results"] = len(result["listings"])
                    if isinstance(result.get("status"), str):
                        entry["summary"]["outcome"] = result["status"]
                else:
                    entry["status"] = "ok"
                return result
            finally:
                entry["ms"] = round((time.perf_counter() - t0) * 1000, 1)
                self.timeline.append(entry)

        return wrapper

    def _seen(self, *rows: dict[str, Any] | None) -> None:
        for r in rows:
            if r and r.get("id") and r["id"] not in self.seen_ids:
                self.seen_ids.append(r["id"])

    def _listing(self, name_or_id: str | None) -> dict[str, Any] | None:
        p = listings.resolve(name_or_id)
        if p is None and not name_or_id and self.open_listing:
            p = listings.get(self.open_listing["id"])
        return p

    # ── the tools ────────────────────────────────────────────────────────────

    def build(self, names: tuple[str, ...] | list[str]) -> list[Callable]:
        box = self

        @tool
        @box._timed
        def search_listings(
            query: str,
            area: str | None = None,
            developer: str | None = None,
            property_type: str | None = None,
            bedrooms: int | None = None,
            min_bedrooms: int | None = None,
            min_price_aed: int | None = None,
            max_price_aed: int | None = None,
            sort_by_yield: bool = False,
        ) -> dict:
            """Search the DubaiNest catalogue (20 listings by DAMAC, Emaar and Nakheel).

            Args:
                query: The visitor's request in their own words, including any budget,
                    area, size or lifestyle details (e.g. "2BR in Business Bay under 3M",
                    "beach and lagoon living for a family").
                area: Optional area name, e.g. "Palm Jumeirah", "Business Bay".
                developer: Optional developer: DAMAC, Emaar or Nakheel.
                property_type: Optional: apartment, villa, townhouse, penthouse or studio.
                bedrooms: Optional exact bedroom count (0 = studio).
                min_bedrooms: Optional minimum bedroom count.
                min_price_aed: Optional minimum price in AED.
                max_price_aed: Optional maximum price in AED (the budget).
                sort_by_yield: True to rank by rental yield instead of price.
            """
            filters = listings.parse_filters(query)
            extra = " ".join(x for x in (area, developer, property_type) if x)
            for k, v in listings.parse_filters(extra).items():
                filters.setdefault(k, v)
            for key, val in (
                ("bedrooms", bedrooms),
                ("min_bedrooms", min_bedrooms),
                ("min_price", min_price_aed),
                ("max_price", max_price_aed),
            ):
                if val is not None and key not in filters:
                    filters[key] = int(val)
            if sort_by_yield:
                filters["sort"] = "yield"
            named = [listings.summary(p) for p in listings.mentioned_listings(query)]
            semantic: dict[str, float] | None = None
            vec: dict[str, Any] = {}
            also: tuple[str, dict[str, float] | None] | None = None
            if not named and not any(k in filters for k in listings.FILTER_KEYS):
                hits, vec = vector_index.rank(query, min_score=0.0)
                semantic = dict(hits) or None
                # Rank with the visitor's own question too, when it is a descriptive
                # search in its own right (no filters) and differs from the query.
                q = box.question.strip()
                if (
                    semantic
                    and q
                    and q.lower() != query.strip().lower()
                    and not any(k in listings.parse_filters(q) for k in listings.FILTER_KEYS)
                ):
                    hits2, _ = vector_index.rank(q, min_score=0.0)
                    also = (q, dict(hits2) or None)
            result = listings.search(filters, query, semantic=semantic, also=also)
            if filters.get("unlisted_areas"):
                result["not_in_catalogue"] = filters["unlisted_areas"]
            if named:
                result["named_listings"] = named
            result["comparison"] = listings.comparison(named or result["listings"])
            budget = filters.get("max_price")
            if budget:
                # Pre-computed so the agent never subtracts (the gate would block it).
                result["budget_max_aed"] = budget
                for row in result["listings"]:
                    gap = budget - row["price_aed"]
                    row["below_budget_by_aed" if gap >= 0 else "above_budget_by_aed"] = abs(gap)
            result["catalogue_size"] = len(listings.all_listings())
            result["golden_visa_min_purchase_aed"] = listings.GOLDEN_VISA_AED
            if vec:
                result["vector_search"] = vec.get("vector")
            box._seen(*result["listings"], *named)
            return box._record("search_listings", result)

        @tool
        @box._timed
        def get_listing(listing: str = "") -> dict:
            """Full details of one listing, plus other listings in the same area.

            Args:
                listing: The listing's name (e.g. "DAMAC Islands") or id (e.g. "10").
                    Leave empty for the listing the visitor has open.
            """
            p = box._listing(listing)
            if p is None:
                return box._record("get_listing", {"error": f"No listing called {listing!r}."})
            s = listings.summary(p)
            same_area = [
                listings.summary(q)
                for q in listings.all_listings()
                if q["area"] == p["area"] and q["id"] != p["id"]
            ][:3]
            box._seen(s)
            return box._record(
                "get_listing",
                {
                    "listing": s,
                    "description": p.get("description", "")[:600],
                    "same_area": same_area,
                    "golden_visa_min_purchase_aed": listings.GOLDEN_VISA_AED,
                },
            )

        @tool
        @box._timed
        def compare_listings(listings_to_compare: list[str]) -> dict:
            """Compare 2-4 listings side by side, with the price and size gaps worked out.

            Args:
                listings_to_compare: Listing names or ids, e.g. ["Burj Vista", "Park Heights"].
            """
            rows = [p for x in listings_to_compare[:4] if (p := listings.resolve(x))]
            summaries = [listings.summary(p) for p in rows]
            box._seen(*summaries)
            return box._record(
                "compare_listings",
                {"listings": summaries, "comparison": listings.comparison(summaries)},
            )

        @tool
        @box._timed
        def area_market(areas: list[str] | None = None) -> dict:
            """Dubai market notes per area: price per sq ft, yield range, demand, profile.
            Figures are dated (as_of) and not verified against Dubai Land Department data.

            Args:
                areas: Area names, e.g. ["Business Bay", "Palm Jumeirah"]. Empty = all areas.
            """
            wanted: list[str] = []
            for a in areas or []:
                f = listings.parse_filters(a)
                wanted += f.get("areas", []) + f.get("unlisted_areas", []) or [a]
            if not wanted and box.open_listing:
                wanted = [box.open_listing["area"]]
            wanted = sorted(set(wanted)) or sorted(area_stats.load()["areas"])
            stats = area_stats.for_areas(wanted)
            stats["dubainest_listings_per_area"] = {
                a: sum(1 for p in listings.all_listings() if p["area"] == a) for a in wanted
            }
            stats["golden_visa_min_purchase_aed"] = listings.GOLDEN_VISA_AED
            return box._record("area_market", stats)

        @tool
        @box._timed
        def calculate_roi(
            listing: str = "",
            non_resident: bool = False,
            price_aed: int | None = None,
            gross_yield_pct: float | None = None,
            size_sqft: int | None = None,
        ) -> dict:
            """Rental yield, service charges, mortgage, cash flow and payback for a listing,
            with the assumptions used. Every ROI figure must come from this tool.

            Args:
                listing: Listing name or id. Leave empty for the listing the visitor has open,
                    or to calculate for a price the visitor gave.
                non_resident: True if the buyer is a non-resident (lower mortgage LTV).
                price_aed: Only for a hypothetical price the visitor gave (no listing).
                gross_yield_pct: Only with price_aed: the yield to assume, e.g. 6.5.
                size_sqft: Only with price_aed: the size, for service charges.
            """
            p = box._listing(listing) if listing or not price_aed else None
            if p is not None:
                result: dict[str, Any] = roi.for_listing(p, non_resident)
                box._seen(result["listing"])
            elif price_aed and gross_yield_pct:
                result = {
                    "hypothetical": True,
                    **roi.roi_numbers(
                        int(price_aed), float(gross_yield_pct), size_sqft, non_resident
                    ),
                }
            else:
                result = {"error": "Name a listing, or give a price and a yield to assume."}
            result["assumptions"] = roi.ASSUMPTIONS
            return box._record("calculate_roi", result)

        @tool
        @box._timed
        def request_viewing(
            listing: str = "",
            preferred_time: str = "",
            name: str = "",
            email: str = "",
            phone: str = "",
        ) -> dict:
            """Prepare a viewing request. It is NOT sent until the visitor replies yes.
            Pass only details the visitor has written themselves; leave the rest empty.

            Args:
                listing: Listing name or id (empty = the listing the visitor has open).
                preferred_time: When they want to visit, as they said it (e.g. "Saturday 11am").
                name: The visitor's name, as they wrote it.
                email: Their email address, as they wrote it.
                phone: Their phone number, as they wrote it.
            """
            p = box._listing(listing)
            said = box.user_text.lower()
            rejected = []
            if name and name.lower() not in said:
                rejected.append("name")
                name = ""
            if email and (not _EMAIL.fullmatch(email.strip()) or email.lower() not in said):
                rejected.append("email")
                email = ""
            if phone and (len(_digits(phone)) < 7 or _digits(phone) not in _digits(box.user_text)):
                rejected.append("phone")
                phone = ""
            missing = [
                k
                for k, ok in (
                    ("property", p is not None),
                    ("date and time", bool(preferred_time.strip())),
                    ("name", bool(name.strip())),
                    ("email or phone", bool(email or phone)),
                )
                if not ok
            ]
            request = {
                "property": p["title"] if p else None,
                "link": f"/property/{p['id']}" if p else None,
                "when": preferred_time.strip() or None,
                "name": name.strip() or None,
                "email": email.strip() or None,
                "phone": phone.strip() or None,
            }
            if missing:
                out: dict[str, Any] = {"status": "need_details", "missing": missing}
            else:
                assert p is not None
                box.pending_viewing = {
                    "property_id": p["id"],
                    "property_title": p["title"],
                    "when": request["when"],
                    "name": request["name"],
                    "email": request["email"],
                    "phone": request["phone"],
                }
                out = {
                    "status": "confirm",
                    "instruction": f"Restate the request and end with exactly: {CONFIRM_PHRASE}",
                }
            if rejected:
                out["not_from_visitor"] = rejected  # the model offered details the user never typed
            out["request"] = request
            if p:
                s = listings.summary(p)
                box._seen(s)
                out["listing"] = {k: s[k] for k in ("title", "area", "price_aed", "link")}
            return box._record("request_viewing", out)

        @tool
        @box._timed
        def catalogue_overview() -> dict:
            """What DubaiNest can help with, and the catalogue by developer, area and price."""
            rows = listings.all_listings()

            def by(key: str) -> dict[str, int]:
                return {
                    v: sum(1 for p in rows if p[key] == v) for v in sorted({p[key] for p in rows})
                }

            return box._record(
                "catalogue_overview",
                {
                    "can_help_with": [
                        "finding listings",
                        "Dubai market and area trends",
                        "ROI, yield and mortgage estimates",
                        "requesting a viewing",
                    ],
                    "listings": len(rows),
                    "by_developer": by("developer"),
                    "by_area": by("area"),
                    "price_range_aed": [rows[0]["price"], rows[-1]["price"]],
                },
            )

        available = {
            "search_listings": search_listings,
            "get_listing": get_listing,
            "compare_listings": compare_listings,
            "area_market": area_market,
            "calculate_roi": calculate_roi,
            "request_viewing": request_viewing,
            "catalogue_overview": catalogue_overview,
        }
        return [available[n] for n in dict.fromkeys(names)]


def tool_names(intents: Sequence[str]) -> tuple[str, ...]:
    names: list[str] = []
    for i in intents:
        names += TOOLS_BY_INTENT.get(i, TOOLS_BY_INTENT["general"])
    return tuple(dict.fromkeys(names))
