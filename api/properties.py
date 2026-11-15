"""GET /api/properties, /api/properties/facets, /api/properties/{id}.

Photo file names are prefixed with MEDIA_BASE_URL. The catalogue is 20 items,
so instead of a query per request each instance scans the table once, keeps it
for PROPERTIES_CACHE_SECONDS (default 60), and filters in Python. The agent does
the same at container start. Results are sorted by price.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import boto3
from fastapi import APIRouter, HTTPException, Query

from . import settings
from .models import Facets, Property

router = APIRouter(prefix="/api/properties", tags=["properties"])

# Catalogue images are stored as ".../media/photos/<filename>" (data/mock_properties.py).
_MEDIA_MARKER = "/media/photos/"
_FLOAT_FIELDS = {"roi", "lat", "lng"}


# ── DynamoDB item → Property ──────────────────────────────────────────────────
# Key schema: PK propertyId; price is priceAed.
def item_to_property(item: dict[str, Any]) -> Property:
    data = dict(item)
    data["id"] = data.pop("propertyId")
    data["price"] = int(data.pop("priceAed"))
    for key, value in list(data.items()):
        if isinstance(value, Decimal):
            data[key] = float(value) if key in _FLOAT_FIELDS else int(value)
    return Property.model_validate(data)


def apply_media_base(prop: Property) -> Property:
    """Point our own photos at MEDIA_BASE_URL; leave other URLs alone.

    The table may hold a different host (e.g. the old localhost dev mount), so
    only the filename after /media/photos/ is kept. Unset locally = no change.
    """
    base = settings.media_base_url()
    if not base:
        return prop
    base = base.rstrip("/")
    images = []
    for url in prop.images:
        path = urlsplit(url).path
        if _MEDIA_MARKER in path:
            images.append(f"{base}/{path.split(_MEDIA_MARKER, 1)[1]}")
        else:
            images.append(url)
    return prop.model_copy(update={"images": images})


# ── Catalogue cache ───────────────────────────────────────────────────────────
class Catalogue:
    """All listings from one Scan, reused for `ttl` seconds per instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Property] = []
        self._loaded_at = float("-inf")

    def clear(self) -> None:
        with self._lock:
            self._loaded_at = float("-inf")

    def all(self) -> list[Property]:
        with self._lock:
            if time.monotonic() - self._loaded_at > settings.cache_seconds():
                self._items = self._scan()
                self._loaded_at = time.monotonic()
            return self._items

    def get(self, property_id: str) -> Property | None:
        return next((p for p in self.all() if p.id == property_id), None)

    @staticmethod
    def _scan() -> list[Property]:
        table = boto3.resource("dynamodb", region_name=settings.region()).Table(
            settings.properties_table()
        )
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        props = [item_to_property(i) for i in items]
        props.sort(key=lambda p: (p.price, p.id))
        return props


CATALOGUE = Catalogue()


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("", response_model=list[Property])
def list_properties(
    area: str | None = Query(default=None),
    developer: str | None = Query(default=None),
    type: str | None = Query(default=None),  # noqa: A002 (query param name the UI sends)
    bedrooms: int | None = Query(default=None, ge=0),
    min_price: int | None = Query(default=None, ge=0, alias="minPrice"),
    max_price: int | None = Query(default=None, ge=0, alias="maxPrice"),
) -> list[Property]:
    results = [
        p
        for p in CATALOGUE.all()
        if (not area or p.area == area)
        and (not developer or p.developer == developer)
        and (not type or p.type == type)
        and (bedrooms is None or p.bedrooms == bedrooms)
        and (min_price is None or p.price >= min_price)
        and (max_price is None or p.price <= max_price)
    ]
    return [apply_media_base(p) for p in results]


@router.get("/facets", response_model=Facets)
def get_facets() -> Facets:
    """Distinct values for the filter dropdowns."""
    props = CATALOGUE.all()
    return Facets(
        areas=sorted({p.area for p in props}),
        developers=sorted({p.developer for p in props}),
        types=sorted({p.type for p in props}),
        bedrooms=sorted({p.bedrooms for p in props}),
    )


@router.get("/{property_id}", response_model=Property)
def get_property(property_id: str) -> Property:
    prop = CATALOGUE.get(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return apply_media_base(prop)
