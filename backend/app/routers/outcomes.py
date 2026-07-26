"""How the alerts actually performed, and CSV export.

  GET /api/outcomes/summary   -> per-source averages and hit rate
  GET /api/outcomes/recent    -> the tracked alerts themselves
  GET /api/outcomes/groups    -> premium groups ranked by how their calls did
  GET /api/outcomes/export.csv
  GET /api/alerts/export.csv  (mounted here to keep the CSV writer in one file)
"""

from __future__ import annotations

import csv
import io
import re
import time
from typing import Any, Iterable

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from .. import db, outcomes
from ..util import ist_date_str

router = APIRouter(prefix="/api/outcomes", tags=["outcomes"])


def _csv(rows: Iterable[dict], columns: list[str], filename: str) -> StreamingResponse:
    """Stream rows as CSV.

    Excel decides a file's encoding from a BOM, and these contain token names
    with non-ASCII characters, so utf-8-sig is used rather than plain utf-8.
    """
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary")
async def summary(days: int = Query(7, ge=1, le=90)):
    return await outcomes.summary(days=days)


@router.get("/recent")
async def recent(limit: int = Query(50, le=500), source: str | None = None):
    return {"items": await outcomes.recent(limit=limit, source=source)}


@router.get("/groups")
async def groups(days: int = Query(30, ge=1, le=90),
                 min_calls: int = Query(1, ge=1)):
    """Premium groups ranked by the outcome of their calls."""
    return {"days": days, "items": await outcomes.by_group(days=days, min_calls=min_calls)}


@router.get("/export.csv")
async def export_outcomes(days: int = Query(30, ge=1, le=90)):
    docs = await outcomes.recent(limit=5000)
    cutoff = time.time() - days * 86400
    rows = []
    for d in docs:
        if d.get("created_at", 0) < cutoff:
            continue
        checks = d.get("checks") or {}
        rows.append({
            "date": ist_date_str(d.get("created_at", 0)),
            "source": d.get("source"),
            "chain": d.get("chain"),
            "symbol": d.get("symbol"),
            "address": d.get("address"),
            "groups": ", ".join(d.get("groups") or []),
            "entry_price": d.get("entry_price"),
            **{f"{label}_pct": (checks.get(label) or {}).get("change_pct")
               for label, _s in outcomes.CHECKPOINTS},
            "best_pct": d.get("best_pct"),
        })
    cols = (["date", "source", "chain", "symbol", "address", "groups", "entry_price"]
            + [f"{l}_pct" for l, _s in outcomes.CHECKPOINTS] + ["best_pct"])
    return _csv(rows, cols, f"outcomes-{ist_date_str(time.time())}.csv")


# ── CSV for the other two tables people actually want out of the dashboard ────

alerts_csv = APIRouter(prefix="/api/alerts", tags=["alerts"])


@alerts_csv.get("/export.csv")
async def export_alerts(chain: str | None = None, q: str | None = None,
                        days: int = Query(15, ge=1, le=90)):
    flt: dict[str, Any] = {"created_at": {"$gte": time.time() - days * 86400}}
    if chain:
        flt["chain"] = chain
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{"token_symbol": rx}, {"token_address": rx}, {"message": rx}]
    docs = await db.get_collection("alerts").find(flt).to_list(5000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    rows = [{
        "date": ist_date_str(d.get("created_at", 0)),
        "time_utc": time.strftime("%H:%M:%S", time.gmtime(d.get("created_at", 0))),
        "type": d.get("type"), "chain": d.get("chain"),
        "symbol": d.get("token_symbol"), "address": d.get("token_address"),
        "sol_symbol": d.get("sol_symbol"), "sol_address": d.get("sol_address"),
        "dex": d.get("dex"), "message": d.get("message"),
    } for d in docs]
    cols = ["date", "time_utc", "type", "chain", "symbol", "address",
            "sol_symbol", "sol_address", "dex", "message"]
    return _csv(rows, cols, f"alerts-{ist_date_str(time.time())}.csv")


detections_csv = APIRouter(prefix="/api/forwarder", tags=["forwarder"])


@detections_csv.get("/detections/export.csv")
async def export_detections(chain: str = Query("eth", pattern="^(eth|rbh|sol)$")):
    docs = await db.get_collection("premium_detections").find({"chain": chain}).to_list(5000)
    docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
    rows = [{
        "date": ist_date_str(d.get("ts", 0)),
        "time_utc": time.strftime("%H:%M:%S", time.gmtime(d.get("ts", 0))),
        "symbol": d.get("symbol"), "name": d.get("name"),
        "address": d.get("address"),
        "groups": ", ".join(d.get("groups") or []),
        "group_count": d.get("count", 1),
        "keyword": d.get("keyword") or "",
    } for d in docs]
    cols = ["date", "time_utc", "symbol", "name", "address",
            "groups", "group_count", "keyword"]
    return _csv(rows, cols, f"detections-{chain}-{ist_date_str(time.time())}.csv")
