"""Deterministic ROI maths (spec §5): yield, service charges, mortgage, cash flow, payback.

Ported unchanged from the step-3 `roi` specialist; the roi_agent calls it through
the `calculate_roi` tool, so every figure the agent quotes comes from here.
"""

from __future__ import annotations

from typing import Any

from . import area_stats, listings

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
        "monthly_rent_aed": round(rent / 12),
        "annual_service_charges_aed": round(service),
        "net_annual_rent_aed": round(net),
        "monthly_net_rent_aed": round(net / 12),
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
            "annual_mortgage_payments_aed": round(monthly * 12),
            "annual_cash_flow_aed": round(cash_flow),
            "monthly_cash_flow_aed": round(cash_flow / 12),
            "cash_on_cash_pct": round(cash_flow / (down + fee) * 100, 2),
        },
        "rent_at_yield_aed": {f"{y}%": round(price * y / 100) for y in (4, 5, 6)},
    }


def for_listing(p: dict[str, Any], non_resident: bool = False) -> dict[str, Any]:
    """ROI for one listing; off-plan listings use their area's benchmark yield."""
    s = listings.summary(p)
    entry: dict[str, Any] = {
        "listing": {k: s[k] for k in ("id", "title", "area", "status", "size_sqft", "link")}
    }
    y, basis = p["roi"], "listing's estimated gross yield"
    if not y:
        y, basis = (
            area_stats.yield_midpoint(p["area"]),
            "area benchmark midpoint (off-plan listing)",
        )
    if y:
        entry["yield_basis"] = basis
        entry.update(roi_numbers(p["price"], y, p["sizeSqft"], non_resident))
    else:
        entry["price_aed"] = p["price"]
        entry["note"] = "No yield estimate: off-plan listing in an area with no benchmark."
    return entry
