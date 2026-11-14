"""The four specialists plus `general`. Plain Python, no model calls (spec §5).

Each returns {"facts": {<its key>: ...}} — the writer may quote only these.
"""

from __future__ import annotations

import re
from typing import Any

from ..prompts import CONFIRM_PHRASE
from ..state import AgentState
from ..tools import area_stats, leads, listings
from .common import AFFIRMATIVE, context_text, history, last

# ── property_search ───────────────────────────────────────────────────────────


def _comparison(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Figures the writer would otherwise work out itself (and so not be grounded):
    lowest/highest across the results, and the gaps when exactly two are compared."""
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


def property_search(state: AgentState) -> dict[str, Any]:
    text = context_text(state)
    filters = listings.parse_filters(text)
    named = [listings.summary(p) for p in listings.mentioned_listings(state["prompt"])]
    facts: dict[str, Any] = {}

    has_filters = any(k in filters for k in (*listings.FILTER_KEYS, "unlisted_areas"))
    open_listing = state.get("property_context")
    if open_listing and not has_filters and not named:
        # A question about the listing open in the UI: it, plus others in its area.
        facts["listing"] = open_listing
        comps = [
            listings.summary(p)
            for p in listings.all_listings()
            if p["area"] == open_listing["area"] and p["id"] != open_listing["id"]
        ]
        facts["same_area"] = comps[:4]
    else:
        result = listings.search(filters, text)
        if filters.get("unlisted_areas"):
            result["not_in_catalogue"] = filters["unlisted_areas"]
        facts.update(result)
        if named:
            facts["named_listings"] = named
    facts["comparison"] = _comparison(named or facts.get("listings") or [])
    facts["catalogue_size"] = len(listings.all_listings())
    facts["golden_visa_min_purchase_aed"] = listings.GOLDEN_VISA_AED
    return {
        "facts": {"property_search": facts},
        "trace": [{"node": "property_search", "matches": facts.get("total_matches")}],
    }


# ── market_intel ──────────────────────────────────────────────────────────────


def market_intel(state: AgentState) -> dict[str, Any]:
    text = context_text(state)
    f = listings.parse_filters(text)
    areas = list(f.get("areas", [])) + list(f.get("unlisted_areas", []))
    areas += [p["area"] for p in listings.mentioned_listings(text)]
    if state.get("property_context"):
        areas.append(state["property_context"]["area"])
    areas = sorted(set(areas)) or sorted(area_stats.load()["areas"])
    stats = area_stats.for_areas(areas)
    listed = {a: sum(1 for p in listings.all_listings() if p["area"] == a) for a in areas}
    stats["dubainest_listings_per_area"] = listed
    return {"facts": {"market_intel": stats}, "trace": [{"node": "market_intel", "areas": areas}]}


# ── roi ───────────────────────────────────────────────────────────────────────

ASSUMPTIONS = {
    "service_charge_aed_per_sqft": 18,  # mid of the AED 12-25 range in area_stats.json
    "mortgage_rate_pct": 4.5,  # illustrative fixed rate, not a bank quote
    "mortgage_years": 25,
    "ltv_pct_resident": 75,
    "ltv_pct_non_resident": 65,
    "dld_transfer_fee_pct": 4,
}


def _monthly_payment(loan: float, rate_pct: float, years: float) -> float:
    r, n = rate_pct / 100 / 12, years * 12
    return loan / n if r == 0 else loan * r / (1 - (1 + r) ** -n)


def roi_numbers(
    price: int, gross_yield_pct: float, size_sqft: int | None, non_resident: bool = False
) -> dict[str, Any]:
    a = ASSUMPTIONS
    ltv = a["ltv_pct_non_resident"] if non_resident else a["ltv_pct_resident"]
    rent = price * gross_yield_pct / 100
    service = (size_sqft or 0) * a["service_charge_aed_per_sqft"]
    net = rent - service
    loan = price * ltv / 100
    down = price - loan
    fee = price * a["dld_transfer_fee_pct"] / 100
    monthly = _monthly_payment(loan, a["mortgage_rate_pct"], a["mortgage_years"])
    cash_flow = net - monthly * 12
    return {
        "price_aed": price,
        "gross_yield_pct": gross_yield_pct,
        "annual_rent_aed": round(rent),
        "annual_service_charges_aed": round(service),
        "net_annual_rent_aed": round(net),
        "net_yield_pct": round(net / price * 100, 2),
        "cash_purchase": {
            "upfront_aed": round(price + fee),
            "payback_years": round((price + fee) / net, 1) if net > 0 else None,
        },
        "mortgage": {
            "ltv_pct": ltv,
            "loan_aed": round(loan),
            "down_payment_aed": round(down),
            "dld_fee_aed": round(fee),
            "cash_needed_aed": round(down + fee),
            "monthly_payment_aed": round(monthly),
            "annual_cash_flow_aed": round(cash_flow),
            "cash_on_cash_pct": round(cash_flow / (down + fee) * 100, 2),
        },
        "rent_at_yield_aed": {f"{y}%": round(price * y / 100) for y in (4, 5, 6)},
    }


def _roi_targets(state: AgentState) -> list[dict[str, Any]]:
    if state.get("property_context"):
        return [listings.get(state["property_context"]["id"])]
    named = listings.mentioned_listings(state["prompt"])
    if named:
        return named[:3]
    filters = listings.parse_filters(context_text(state))
    if any(k in filters for k in listings.FILTER_KEYS):
        ids = [s["id"] for s in listings.search({**filters, "sort": "yield"})["listings"][:3]]
        return [listings.get(i) for i in ids]
    for turn in reversed(history(state)):  # "what's its yield?" after a listing was discussed
        named = listings.mentioned_listings(turn["content"])
        if named:
            return named[:1]
    return []


def roi(state: AgentState) -> dict[str, Any]:
    non_resident = (
        "non-resident" in state["prompt"].lower() or "non resident" in state["prompt"].lower()
    )
    results = []
    for p in _roi_targets(state):
        s = listings.summary(p)
        y, basis = p["roi"], "listing's estimated gross yield"
        if not y:
            y, basis = (
                area_stats.yield_midpoint(p["area"]),
                "area benchmark midpoint (off-plan listing)",
            )
        entry: dict[str, Any] = {
            "listing": {k: s[k] for k in ("id", "title", "area", "status", "size_sqft", "link")}
        }
        if y:
            entry["yield_basis"] = basis
            entry.update(roi_numbers(p["price"], y, p["sizeSqft"], non_resident))
        else:
            entry["price_aed"] = p["price"]
            entry["note"] = "No yield estimate: off-plan listing in an area with no benchmark."
        results.append(entry)
    facts: dict[str, Any] = {"assumptions": ASSUMPTIONS, "results": results}
    if not results:
        f = listings.parse_filters(context_text(state))
        facts["note"] = "No specific listing in the question; area benchmarks only."
        facts["benchmarks"] = area_stats.for_areas(
            f.get("areas") or sorted(area_stats.load()["areas"])
        )
    return {"facts": {"roi": facts}, "trace": [{"node": "roi", "listings": len(results)}]}


# ── scheduler ─────────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)(\+?\d[\d\s-]{6,}\d)(?!\w)")
# Prefix in any case ("I'm", "my name is"); the name itself must be capitalised.
_NAME = re.compile(
    r"\b(?i:my name is|i am|i'm|i’m|this is|name:)\s+([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2})"
)
_WHEN = re.compile(
    r"\b((?:next\s+)?(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:day)?|today|tomorrow|this weekend|"
    r"next week|weekend|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b"
    r"(?:[^.?!\n]{0,20}?\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|morning|afternoon|evening|noon))?",
    re.I,
)


def _details(state: AgentState) -> dict[str, Any]:
    """Latest value of each detail across the user's turns (newest wins)."""
    turns = [t["content"] for t in history(state) if t.get("role") == "user"] + [state["prompt"]]
    d: dict[str, Any] = {}
    for text in turns:
        if m := _EMAIL.search(text):
            d["email"] = m.group(0)
        if m := _PHONE.search(_EMAIL.sub(" ", text)):
            d["phone"] = re.sub(r"[\s-]", "", m.group(1))
        if m := _NAME.search(text):
            d["name"] = m.group(1)
        if m := _WHEN.search(text):
            d["when"] = " ".join(x for x in m.groups() if x)
        if named := listings.mentioned_listings(text):
            d["property"] = named[0]
    if state.get("property_context"):
        d.setdefault("property", listings.get(state["property_context"]["id"]))
    return d


def scheduler(state: AgentState) -> dict[str, Any]:
    d = _details(state)
    missing = [
        k
        for k, ok in (
            ("property", "property" in d),
            ("date and time", "when" in d),
            ("name", "name" in d),
            ("email or phone", "email" in d or "phone" in d),
        )
        if not ok
    ]
    request = {
        "property": d["property"]["title"] if "property" in d else None,
        "link": f"/property/{d['property']['id']}" if "property" in d else None,
        "when": d.get("when"),
        "name": d.get("name"),
        "email": d.get("email"),
        "phone": d.get("phone"),
    }
    confirming = CONFIRM_PHRASE in last(state, "assistant") and AFFIRMATIVE.match(state["prompt"])
    extra: dict[str, Any]
    if missing:
        status, extra = "need_details", {"missing": missing}
    elif confirming:
        lead_id = leads.save_viewing_request(
            {
                "property_id": d["property"]["id"],
                "property_title": d["property"]["title"],
                "when": d["when"],
                "name": d["name"],
                "email": d.get("email"),
                "phone": d.get("phone"),
            }
        )
        status, extra = "saved", {"reference": lead_id[:8].upper()}
    else:
        status, extra = "confirm", {}
    return {
        "facts": {"scheduler": {"status": status, "request": request, **extra}},
        "trace": [{"node": "scheduler", "status": status}],
    }


# ── general ───────────────────────────────────────────────────────────────────


def general(state: AgentState) -> dict[str, Any]:
    rows = listings.all_listings()

    def by(key: str) -> dict[str, int]:
        return {v: sum(1 for p in rows if p[key] == v) for v in sorted({p[key] for p in rows})}

    return {
        "facts": {
            "general": {
                "can_help_with": [
                    "finding listings",
                    "Dubai market and area trends",
                    "ROI, yield and mortgage estimates",
                    "requesting a viewing",
                ],
                "catalogue": {
                    "listings": len(rows),
                    "by_developer": by("developer"),
                    "by_area": by("area"),
                    "price_range_aed": [rows[0]["price"], rows[-1]["price"]],
                },
            }
        },
        "trace": [{"node": "general"}],
    }
