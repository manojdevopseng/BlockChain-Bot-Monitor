"""Analytics routes — aggregates computed from what the scanners actually stored.

Every number and every chart point below is counted from MongoDB. Empty
collections give empty series rather than a synthetic curve: a flat chart here
means nothing has happened yet, which is information in itself.
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_HOUR = 3600


async def _hourly(collection: str, ts_field: str, hours: int = 24) -> list[dict]:
    """Count documents per hour for the last `hours`, oldest bucket first."""
    now = int(time.time())
    start = now - hours * _HOUR
    docs = await db.get_collection(collection).find(
        {ts_field: {"$gte": start}}, {ts_field: 1}
    ).to_list(20000)

    buckets = {start + i * _HOUR: 0 for i in range(hours)}
    for d in docs:
        ts = d.get(ts_field)
        if not isinstance(ts, (int, float)):
            continue
        slot = start + int((ts - start) // _HOUR) * _HOUR
        if slot in buckets:
            buckets[slot] += 1
    return [{"t": t, "value": v} for t, v in sorted(buckets.items())]


@router.get("/summary")
async def summary():
    tokens = db.get_collection("tokens")
    alerts = db.get_collection("alerts")
    day_ago = time.time() - 86400
    return {
        "tokens_detected": await tokens.count_documents({}),
        "tokens_24h": await tokens.count_documents({"created_at": {"$gte": day_ago}}),
        "alerts_total": await alerts.count_documents({}),
        "cross_chain_matches": await alerts.count_documents({"type": "Cross-Chain Match"}),
        "gas_hits": await db.get_collection("gas_alerts").count_documents({}),
        "premium_detections": await db.get_collection("premium_detections").count_documents({}),
    }


@router.get("/activity")
async def activity():
    """Per-hour counts for the last 24h, straight from the collections."""
    return {
        "tokens_detected": await _hourly("tokens", "created_at"),
        "alerts_triggered": await _hourly("alerts", "created_at"),
        "gas_hits": await _hourly("gas_alerts", "created_at"),
        "premium_detections": await _hourly("premium_detections", "ts"),
    }


@router.get("/by-chain")
async def by_chain():
    docs = await db.get_collection("tokens").find({}, {"chain": 1}).to_list(5000)
    counts: dict[str, int] = {}
    for t in docs:
        counts[t.get("chain", "other")] = counts.get(t.get("chain", "other"), 0) + 1
    total = sum(counts.values()) or 1
    return [
        {"chain": k, "count": v, "pct": round(v / total * 100, 1)}
        for k, v in sorted(counts.items(), key=lambda x: -x[1])
    ]
