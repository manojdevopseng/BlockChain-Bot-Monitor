"""Small shared helpers for routers."""

from __future__ import annotations

from typing import Any


# Internal fields never returned to the client: Mongo's ObjectId and the
# datetime the TTL index runs on (the float `ts`/`created_at` carries the time).
_INTERNAL = ("_id", "dt")


def clean(doc: dict | None) -> dict | None:
    """Strip internal fields so responses are JSON-serializable."""
    if not doc:
        return doc
    for k in _INTERNAL:
        doc.pop(k, None)
    return doc


def clean_list(docs: list[dict]) -> list[dict]:
    for d in docs:
        for k in _INTERNAL:
            d.pop(k, None)
    return docs


def pct_change(current: float, previous: float) -> float:
    if not previous:
        return 0.0
    return round((current - previous) / previous * 100, 1)
