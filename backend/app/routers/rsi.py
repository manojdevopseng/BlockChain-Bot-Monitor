"""RSI Tracker — the panel, the token list, and the settings behind both.

The token list is the user's own: nothing is added automatically and there is
no cap. Everything here writes to the same three collections the worker reads
(`rsi_tokens`, `rsi_settings`, `rsi_state`), which is what keeps the panel, the
Settings page and the Telegram commands from disagreeing — there is only one
copy of the answer.

Candles and readings expire through the TTL index on `dt`; the token list does
not, because that is a list you made, not data that went stale.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, registry
from ..rsi_math import DEFAULT_HIGH, DEFAULT_LOW, DEFAULT_PERIOD
from ..scanners import scfg
from ..scanners.rsi_price import chains
from ..scanners.rsi_tracker import (CADENCES, DEFAULT_CADENCE, DEFAULT_INTERVAL,
                                    INTERVAL_LABELS, INTERVALS)
from ..util import clean_list, gmgn_url, ist_date_str

router = APIRouter(prefix="/api/rsi", tags=["rsi"])

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _clean_address(value) -> str:
    addr = str(value or "").strip()
    if not _ADDRESS_RE.match(addr):
        raise HTTPException(400, f"{value!r} is not a contract address")
    return addr.lower()


def _clean_interval(value, default: str = DEFAULT_INTERVAL) -> str:
    interval = str(value or default)
    if interval not in INTERVALS:
        raise HTTPException(400, f"unknown interval '{interval}' — "
                                 f"have {', '.join(INTERVALS)}")
    return interval


@router.get("/chains")
async def rsi_chains():
    """The filter tabs, and whether each chain is actually switched on."""
    enabled = await registry.enabled_map()
    out = []
    for key, spec in chains().items():
        out.append({"id": key, "label": spec.label,
                    "enabled": bool(enabled.get(f"rsi_chain_{key}", True))
                               and bool(enabled.get(f"rsi_rpc_{key}", True)),
                    "own_endpoints": bool(scfg.RSI_ENDPOINTS.get(key))})
    # Listed even though nothing prices it yet, so the tab is there when it does.
    out.append({"id": "sol", "label": "SOL",
                "enabled": bool(enabled.get("rsi_chain_sol", False)),
                "own_endpoints": bool(scfg.RSI_ENDPOINTS.get("sol"))})
    return {"items": out}


@router.get("/settings")
async def get_settings():
    doc = await db.get_collection("rsi_settings").find_one({"_id": "rsi"}) or {}
    enabled = await registry.enabled_map()
    return {
        "low": float(doc.get("low", DEFAULT_LOW)),
        "high": float(doc.get("high", DEFAULT_HIGH)),
        "period": int(doc.get("period", DEFAULT_PERIOD)),
        "cadence": str(doc.get("cadence", DEFAULT_CADENCE)),
        "cadences": list(CADENCES),
        "intervals": [{"id": k, "label": INTERVAL_LABELS[k]} for k in INTERVALS],
        "default_interval": DEFAULT_INTERVAL,
        "retention_days": scfg.RSI_RETENTION_DAYS,
        "alert_chat_set": bool(scfg.RSI_ALERT_CHAT_ID),
        "enabled": bool(enabled.get("rsi_tracker", True)),
    }


@router.patch("/settings")
async def set_settings(payload: dict = Body(...)):
    """Bounds, period and cadence. Whatever is sent is changed; the rest stays."""
    update: dict = {}
    if "low" in payload or "high" in payload:
        current = await db.get_collection("rsi_settings").find_one({"_id": "rsi"}) or {}
        low = float(payload.get("low", current.get("low", DEFAULT_LOW)))
        high = float(payload.get("high", current.get("high", DEFAULT_HIGH)))
        if not (0 < low < high < 100):
            raise HTTPException(400, "bounds must be 0 < low < high < 100")
        update["low"], update["high"] = low, high
    if "period" in payload:
        period = int(payload["period"])
        # Two is the smallest RSI that means anything; past a few hundred the
        # series is longer than the retention window can hold.
        if not 2 <= period <= 200:
            raise HTTPException(400, "period must be between 2 and 200")
        update["period"] = period
    if "cadence" in payload:
        cadence = str(payload["cadence"])
        if cadence not in CADENCES:
            raise HTTPException(400, f"cadence must be one of {', '.join(CADENCES)}")
        update["cadence"] = cadence
    if not update:
        raise HTTPException(400, "nothing to change")
    await db.get_collection("rsi_settings").update_one(
        {"_id": "rsi"}, {"$set": update}, upsert=True)
    return await get_settings()


@router.get("/tokens")
async def tokens(chain: str = Query("all"), q: str | None = None,
                 date: str | None = None, limit: int = Query(200, le=1000)):
    """The panel: every token you added, with its latest reading."""
    flt: dict = {}
    if chain != "all":
        flt["chain"] = chain
    rows = await db.get_collection("rsi_tokens").find(flt).sort("added_at", -1).to_list(1000)

    states = {}
    async for st in db.get_collection("rsi_state").find(
            {} if chain == "all" else {"chain": chain}):
        states[(st.get("chain"), st.get("address"))] = st

    out = []
    for row in rows:
        st = states.get((row.get("chain"), (row.get("address") or "").lower()), {})
        # History: the day is stamped on the reading, so a past day answers
        # "what did this look like then" rather than dropping the row entirely.
        if date and st.get("day") != date:
            continue
        row = {**row,
               "rsi": st.get("rsi"), "zone": st.get("zone") or "",
               "price": st.get("price"), "samples": st.get("samples") or 0,
               "checked_at": st.get("checked_at"), "updated_at": st.get("updated_at"),
               "last_alert_at": st.get("last_alert_at"),
               "interval_label": INTERVAL_LABELS.get(row.get("interval"), ""),
               "gmgn_url": gmgn_url(row.get("chain", ""), row.get("address", ""))}
        if q:
            ql = q.lower()
            if not any(ql in str(row.get(f) or "").lower()
                       for f in ("address", "symbol", "name")):
                continue
        out.append(row)
    return {"total": len(out), "items": clean_list(out[:limit])}


@router.post("/tokens")
async def add_token(payload: dict = Body(...)):
    """Add a token to watch. No limit, and nothing is added on its own."""
    chain = str(payload.get("chain") or "").lower()
    if chain not in {**chains(), "sol": None}:
        raise HTTPException(400, f"unknown chain '{chain}'")
    address = _clean_address(payload.get("address"))
    interval = _clean_interval(payload.get("interval"))
    now = time.time()
    await db.get_collection("rsi_tokens").update_one(
        {"chain": chain, "address": address},
        {"$set": {"chain": chain, "address": address, "interval": interval,
                  "symbol": str(payload.get("symbol") or "")[:32],
                  "name": str(payload.get("name") or "")[:64],
                  "enabled": True, "added_at": now, "day": ist_date_str(now)}},
        upsert=True,
    )
    return {"chain": chain, "address": address, "interval": interval, "added": True}


@router.patch("/tokens/{address}")
async def edit_token(address: str, payload: dict = Body(...)):
    """Change one token's own interval, its period, or switch it off."""
    update: dict = {}
    if "interval" in payload:
        update["interval"] = _clean_interval(payload["interval"])
    if "period" in payload:
        period = int(payload["period"])
        if not 2 <= period <= 200:
            raise HTTPException(400, "period must be between 2 and 200")
        update["period"] = period
    if "enabled" in payload:
        update["enabled"] = bool(payload["enabled"])
    if not update:
        raise HTTPException(400, "nothing to change")
    res = await db.get_collection("rsi_tokens").update_one(
        {"address": _clean_address(address)}, {"$set": update})
    if not res.matched_count:
        raise HTTPException(404, f"{address} is not being tracked")
    # Its candles belong to the old interval; a new one starts warming up.
    if "interval" in update:
        await db.get_collection("rsi_candles").delete_many(
            {"address": _clean_address(address),
             "interval": {"$ne": update["interval"]}})
    return {"address": address, **update}


@router.delete("/tokens/{address}")
async def remove_token(address: str):
    addr = _clean_address(address)
    res = await db.get_collection("rsi_tokens").delete_one({"address": addr})
    if not res.deleted_count:
        raise HTTPException(404, f"{address} is not being tracked")
    await db.get_collection("rsi_candles").delete_many({"address": addr})
    await db.get_collection("rsi_state").delete_many({"address": addr})
    return {"address": addr, "removed": True}


@router.get("/dates")
async def dates(chain: str = Query("all")):
    from datetime import datetime
    flt = {} if chain == "all" else {"chain": chain}
    days = [d for d in await db.get_collection("rsi_state").distinct("day", flt) if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"),
                            reverse=True)}


@router.get("/stats")
async def stats():
    col = db.get_collection("rsi_tokens")
    state = db.get_collection("rsi_state")
    return {
        "total": await col.count_documents({}),
        "oversold": await state.count_documents({"zone": "oversold"}),
        "overbought": await state.count_documents({"zone": "overbought"}),
        "candles": await db.get_collection("rsi_candles").count_documents({}),
        "retention_days": scfg.RSI_RETENTION_DAYS,
    }
