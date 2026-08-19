"""How the alerts actually performed, and CSV export.

  GET /api/outcomes/summary   -> per-source averages and hit rate
  GET /api/outcomes/recent    -> the tracked alerts themselves
  GET /api/outcomes/groups    -> premium groups ranked by how their calls did
  GET /api/outcomes/export.csv
  GET /api/alerts/export.csv  (mounted here to keep the CSV writer in one file)
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

from .. import csvout, db, outcomes
from ..util import ist_date_str

router = APIRouter(prefix="/api/outcomes", tags=["outcomes"])


@router.get("/summary")
async def summary(days: int = Query(7, ge=1, le=90)):
    return await outcomes.summary(days=days)


@router.get("/recent")
async def recent(limit: int = Query(50, le=500), source: str | None = None):
    return {"items": await outcomes.recent(limit=limit, source=source)}


@router.get("/groups")
async def groups(days: int = Query(30, ge=1, le=90),
                 min_calls: int = Query(5, ge=1)):
    """Premium groups ranked by the outcome of their calls.

    `min_calls` defaults to 5 because one lucky call reads as a 100% hit rate
    and sorts straight to the top, which is exactly the wrong group to trust.
    Five is still thin — twenty or more is where the ranking starts meaning
    something — but it keeps single-call noise out of the table.
    """
    return {"days": days, "items": await outcomes.by_group(days=days, min_calls=min_calls)}


@router.get("/export.csv")
async def export_outcomes(days: int = Query(0, ge=0, le=365)):
    """Every tracked alert still held. `days=0` means the whole window."""
    flt: dict = {}
    if days:
        flt["created_at"] = {"$gte": time.time() - days * 86400}

    cols = (["date", "source", "chain", "symbol", "address", "groups", "entry_price"]
            + [f"{l}_pct" for l, _s in outcomes.CHECKPOINTS] + ["best_pct"])

    def row(d: dict) -> dict:
        checks = d.get("checks") or {}
        return {
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
        }

    rows = csvout.paged(db.get_collection("outcomes"), flt,
                        sort_key="created_at")
    return csvout.csv_response(rows, cols,
                               f"outcomes-{ist_date_str(time.time())}.csv", row=row)


# ── CSV for the other two tables people actually want out of the dashboard ────

alerts_csv = APIRouter(prefix="/api/alerts", tags=["alerts"])


@alerts_csv.get("/export.csv")
async def export_alerts(chain: str | None = None, q: str | None = None,
                        days: int = Query(0, ge=0, le=365)):
    """Every alert still held, unless a window is asked for.

    `days=0` — the default the page now sends — means "whatever retention has
    kept", which is the honest answer to a download button. It used to default
    to 15 and stop at 5,000 rows, so a busy fortnight exported as a truncated
    file with nothing to say it had been cut.
    """
    flt: dict[str, Any] = {}
    if days:
        flt["created_at"] = {"$gte": time.time() - days * 86400}
    if chain:
        flt["chain"] = chain
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{"token_symbol": rx}, {"token_address": rx}, {"message": rx}]

    cols = ["date", "time_utc", "type", "chain", "symbol", "address",
            "sol_symbol", "sol_address", "dex", "message"]

    def row(d: dict) -> dict:
        return {
            "date": ist_date_str(d.get("created_at", 0)),
            "time_utc": time.strftime("%H:%M:%S", time.gmtime(d.get("created_at", 0))),
            "type": d.get("type"), "chain": d.get("chain"),
            "symbol": d.get("token_symbol"), "address": d.get("token_address"),
            "sol_symbol": d.get("sol_symbol"), "sol_address": d.get("sol_address"),
            "dex": d.get("dex"), "message": d.get("message"),
        }

    rows = csvout.paged(db.get_collection("alerts"), flt, sort_key="created_at")
    return csvout.csv_response(rows, cols,
                               f"alerts-{ist_date_str(time.time())}.csv", row=row)


detections_csv = APIRouter(prefix="/api/forwarder", tags=["forwarder"])


@detections_csv.get("/detections/export.csv")
async def export_detections(chain: str = Query("eth", pattern="^(all|eth|rbh|sol|bnb)$"),
                            q: str | None = None, multi: bool = False):
    """Every detection still held — the archive as well as today.

    This was the bigger of the two problems with these exports. The live
    collection is cleared per chain at the start of each day and that day is
    written to premium_archive, so an export that read only the live rows was
    an export of today, however long retention had been keeping things. The
    History dropdown could see the archive; the download could not.

    Ordering is newest day first, and newest row within each day. A true global
    sort would mean holding every row at once, which is the thing this rewrite
    exists to avoid — and the result is the same order in practice, because a
    day's rows never interleave with another day's.
    """
    flt: dict = {} if chain == "all" else {"chain": chain}

    def keep(d: dict) -> bool:
        if multi and int(d.get("count") or 0) < 2:
            return False
        if not q:
            return True
        needle = q.lower()
        return any(needle in str(d.get(k) or "").lower()
                   for k in ("symbol", "name", "address")) or             any(needle in str(g).lower() for g in (d.get("groups") or []))

    async def rows():
        # Today, from the live collection.
        async for d in csvout.paged(db.get_collection("premium_detections"),
                                    flt, sort_key="ts"):
            if keep(d):
                yield d
        # Then the archive, newest day first. One document per chain per day,
        # so these are few — it is the items inside them that are many, and
        # those are yielded one at a time rather than gathered.
        arch = await db.get_collection("premium_archive").find(flt).to_list(2000)
        def when(doc):
            try:
                return datetime.strptime(doc.get("date", ""), "%d-%m-%Y")
            except ValueError:
                return datetime.min
        for day in sorted(arch, key=when, reverse=True):
            for item in (day.get("items") or []):
                item.setdefault("chain", day.get("chain"))
                if keep(item):
                    yield item

    cols = ["date", "time_utc", "chain", "symbol", "name", "address",
            "groups", "group_count", "keyword"]

    def row(d: dict) -> dict:
        return {
            "date": ist_date_str(d.get("ts", 0)),
            "time_utc": time.strftime("%H:%M:%S", time.gmtime(d.get("ts", 0))),
            # Carried in the merged export for the same reason the table gains
            # a column: without it the rows are four chains in one file,
            # unlabelled.
            "chain": d.get("chain", ""),
            "symbol": d.get("symbol"), "name": d.get("name"),
            "address": d.get("address"),
            "groups": ", ".join(d.get("groups") or []),
            "group_count": d.get("count", 1),
            "keyword": d.get("keyword") or "",
        }

    return csvout.csv_response(
        rows(), cols, f"detections-{chain}-{ist_date_str(time.time())}.csv", row=row)
