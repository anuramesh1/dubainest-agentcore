"""Post-check (spec §10): every AED amount in the answer must come from `facts`
(or from the user's own words). Mismatches are logged to the trace; the
grounding eval fails on any."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_UNIT = r"(bn|billion|m|mn|million|k)?"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_RANGE = re.compile(rf"AED\s*{_NUM}\s*[-–]\s*{_NUM}\s*{_UNIT}\b", re.I)
_BEFORE = re.compile(rf"AED\s*{_NUM}\s*{_UNIT}\b", re.I)
_AFTER = re.compile(rf"{_NUM}\s*{_UNIT}\s*AED\b", re.I)
_MULT = {"bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6, "k": 1e3}


def _value(num: str, unit: str | None) -> tuple[float, float]:
    """(amount, tolerance). Abbreviations ('1.85M') may round to their last digit."""
    mult = _MULT.get((unit or "").lower(), 1.0)
    clean = num.replace(",", "")
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    amount = float(clean) * mult
    tol = 0.5 if mult == 1 else max(0.5 * 10 ** (-decimals) * mult, amount * 0.005)
    return amount, tol


def aed_amounts(text: str) -> list[tuple[str, float, float]]:
    """[(as written, amount, tolerance)] for every AED figure in the text."""
    found, spans = [], []
    for m in _RANGE.finditer(text):
        spans.append(m.span())
        for num in (m.group(1), m.group(2)):
            found.append((m.group(0), *_value(num, m.group(3))))
    for rx in (_BEFORE, _AFTER):
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in spans):
                continue
            spans.append(m.span())
            found.append((m.group(0), *_value(m.group(1), m.group(2))))
    return found


def _numbers(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _numbers(v)
    elif isinstance(obj, str):
        for _, amount, _ in aed_amounts(obj):
            yield amount


def ungrounded(answer: str, facts: dict[str, Any], user_text: str = "") -> list[str]:
    # Signs are written in words ("a negative cash flow of AED 13,313"), so compare sizes.
    allowed = {abs(v) for v in _numbers(facts)} | {a for _, a, _ in aed_amounts(user_text)}
    allowed |= {
        float(n.replace(",", "")) * _MULT.get((u or "").lower(), 1)
        for n, u in re.findall(rf"{_NUM}\s*{_UNIT}", user_text, re.I)
        if n
    }
    return [
        written
        for written, amount, tol in aed_amounts(answer)
        if not any(abs(amount - v) <= tol for v in allowed)
    ]
