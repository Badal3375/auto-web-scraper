"""HTML -> structured rows, driven entirely by the field specs in config.yaml."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from .config import FieldSpec, TargetConfig

RATING_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")


def to_number(text: Any) -> float | None:
    """'£1,299.50' -> 1299.5 ; 'Rs. 45' -> 45.0 ; 'n/a' -> None (assumes 1,234.56 style)."""
    if text is None:
        return None
    match = _NUMBER.search(str(text).replace("\u00a0", " "))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def coerce(value: Any, ftype: str) -> Any:
    if value is None:
        return None
    if ftype in ("text", "date"):
        return " ".join(str(value).split()) or None
    if ftype in ("float", "price"):
        return to_number(value)
    if ftype == "int":
        n = to_number(value)
        return None if n is None else int(n)
    if ftype == "rating":
        lowered = str(value).lower()
        for word, score in RATING_WORDS.items():
            if re.search(rf"\b{word}\b", lowered):
                return score
        n = to_number(lowered)
        return None if n is None else (int(n) if n == int(n) else n)
    return value


def _read(el: Tag, spec: FieldSpec, base_url: str) -> str | None:
    if spec.attr:
        val = el.get(spec.attr)
        if isinstance(val, list):  # e.g. class="star-rating Three"
            val = " ".join(val)
    else:
        val = el.get_text(" ", strip=True)
    if val and spec.absolute:
        val = urljoin(base_url, val)
    return val or None


def extract_field(item: Tag, spec: FieldSpec, base_url: str) -> Any:
    if spec.selector:
        found = item.select(spec.selector) if spec.multiple else [e for e in [item.select_one(spec.selector)] if e]
    else:
        found = [item]
    parts = [p for p in (_read(e, spec, base_url) for e in found) if p]
    raw = " | ".join(parts) if parts else None
    if raw and spec.regex:
        m = re.search(spec.regex, raw)
        raw = (m.group(1) if m.groups() else m.group(0)) if m else None
    value = coerce(raw, spec.type)
    return spec.default if value is None or value == "" else value


def parse_items(html: str, base_url: str, target: TargetConfig) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for item in soup.select(target.item_selector):
        row = {spec.name: extract_field(item, spec, base_url) for spec in target.fields}
        if any(v is not None for v in row.values()):
            rows.append(row)
    return rows


def find_next_page(html: str, base_url: str, target: TargetConfig) -> str | None:
    if not target.next_page:
        return None
    el = BeautifulSoup(html, "html.parser").select_one(target.next_page.selector)
    href = el.get(target.next_page.attr) if el else None
    return urljoin(base_url, href) if href else None
