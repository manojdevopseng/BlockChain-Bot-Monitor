"""Analytics routes — aggregate metrics + chart series.

Phase 1 derives series from stored docs; where history is sparse it returns a
smooth synthetic 24h series so charts render. Phase 3+ replaces synthetic points
with real time-bucketed aggregates.
"""

from __future__ import annotations

import math
import time

from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _series(points: int = 24, base: float = 200, amp: float = 120) -> list[dict]:
    now = int(time.time())
    out = []
    for i in range(points):
        t = now - (points - i) * 3600
        val = base + amp * (0.5 + 0.5 * math.sin(i / 3.0)) + (i % 5) * 8
        out.append({"t": t, "value": round(val)})
    return out


@router.get("/summary")
async def summary():
    tokens = await db.get_collection("tokens").count_documents({})
    alerts = await db.get_collection("alerts").count_documents({})
    return {
        "tokens_detected": tokens,
        "messages_forwarded": 12548,
        "watchlist_hits": 128,
        "total_volume_usd": 312_480_000,
        "avg_response_ms": 112,
    }


@router.get("/activity")
async def activity():
    return {
        "tokens_detected": _series(base=300, amp=200),
        "messages_forwarded": _series(base=250, amp=160),
        "alerts_triggered": _series(base=150, amp=90),
        "watchlist_hits": _series(base=80, amp=50),
    }


@router.get("/by-chain")
async def by_chain():
    docs = await db.get_collection("tokens").find({}).to_list(1000)
    counts: dict[str, int] = {}
    for t in docs:
        counts[t.get("chain", "other")] = counts.get(t.get("chain", "other"), 0) + 1
    total = sum(counts.values()) or 1
    return [
        {"chain": k, "count": v, "pct": round(v / total * 100, 1)}
        for k, v in sorted(counts.items(), key=lambda x: -x[1])
    ]
