"""data/area_stats.json: dated market figures, replacing the old hard-coded prompt paragraph."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .. import config


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads((config.DATA_DIR / "area_stats.json").read_text())


def for_areas(areas: list[str]) -> dict[str, Any]:
    data = load()
    return {
        "as_of": data["as_of"],
        "source": data["source"],
        "verified": data["verified"],
        "overview": data["overview"],
        "areas": {
            a: data["areas"].get(
                a,
                {
                    "gross_yield_pct": None,
                    "profile": "No figures for this area in the market notes.",
                },
            )
            for a in areas
        },
    }


def yield_midpoint(area: str) -> float | None:
    rng = load()["areas"].get(area, {}).get("gross_yield_pct")
    return round((rng[0] + rng[1]) / 2, 2) if rng else None
