"""Normalise and de-duplicate parsed rows before they are stored."""
from __future__ import annotations

from typing import Any

from dateutil import parser as dateparser

from .config import TargetConfig


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        value = " ".join(value.replace("\u00a0", " ").split())
        return value or None
    return value


def _to_iso_date(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return dateparser.parse(value).date().isoformat()
    except (ValueError, OverflowError):
        return None


def clean_rows(rows: list[dict[str, Any]], target: TargetConfig) -> list[dict[str, Any]]:
    date_fields = {s.name for s in target.fields if s.type == "date"}
    cleaned: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        row = {k: _norm(v) for k, v in row.items()}
        for name in date_fields:
            row[name] = _to_iso_date(row.get(name))
        if all(v is None for v in row.values()):
            continue
        key = tuple(row.get(k) for k in target.dedupe_on) if target.dedupe_on else tuple(sorted(row.items(), key=str))
        cleaned.setdefault(key, row)  # keep first occurrence
    return list(cleaned.values())
