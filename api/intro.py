"""GET /api/chat/property-intro/{id}: the chat's opening card for a listing.

Templated from the listing's own fields, with no model call (spec §2), ported
from dubainest-ai AgentService.property_intro. POST /api/chat is in chat_relay.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .models import Property, PropertyIntro
from .properties import CATALOGUE

router = APIRouter(prefix="/api/chat", tags=["chat"])


def build_intro(p: Property) -> PropertyIntro:
    per_sqft = int(p.price / p.sizeSqft) if p.sizeSqft else 0
    beds = "Studio" if p.bedrooms == 0 else f"{p.bedrooms}BR"
    off_plan = (p.completionYear or 0) > 2026 or p.roi == 0
    status = (
        f"Off-plan, handover {p.completionYear}."
        if off_plan
        else f"Ready / completed {p.completionYear}."
    )
    intro = (
        f"**{p.title}**\n\n"
        f"{p.developer} · {beds} / {p.bathrooms}BA · {p.sizeSqft:,} sqft · "
        f"AED {p.price:,} (AED {per_sqft:,}/sqft)\n\n"
        f"{p.area} — {status}\n\n"
        "Ask me anything about this property:"
    )
    suggestions = [
        "What's the expected ROI?",
        "Tell me about the location.",
        f"What else is completed in {p.area}?",
        "Is the price fair for this area?",
    ]
    return PropertyIntro(intro=intro, suggestions=suggestions)


@router.get("/property-intro/{property_id}", response_model=PropertyIntro)
def property_intro(property_id: str) -> PropertyIntro:
    prop = CATALOGUE.get(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return build_intro(prop)
