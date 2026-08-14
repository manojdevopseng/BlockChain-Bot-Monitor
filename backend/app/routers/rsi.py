"""RSI Tracker — the panel, the token list, and the settings behind both.

The token list is the user's own: nothing is added automatically and there is
no cap. Everything here writes to the same three collections the worker reads
(`rsi_tokens`, `rsi_settings`, `rsi_state`), which is what keeps the panel, the
Settings page and the Telegram commands from disagreeing — there is only one
copy of the answer.

Candles and readings expire through the TTL index on `dt`; the token list does
not, because that is a list you made, not data that went stale.

Every row in that list belongs to an account and every query here says so. What
is deliberately NOT per account is the work: the price of a token, its candles,
and the RSI computed from them are the same answer for everyone who asked, so
they are keyed by what they depend on (token, timeframe, candle count) and
shared. Only the alert bookkeeping — which zone you have already been told
about — sits on your own row.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import accounts, db, registry, security
from ..rsi_math import DEFAULT_HIGH, DEFAULT_LOW, DEFAULT_PERIOD
from ..scanners import scfg
from ..scanners.rsi_price import chains
from ..scanners.rsi_tracker import (CADENCES, CANDLE_CHOICES, DEFAULT_CADENCE,
                                    DEFAULT_CANDLES, DEFAULT_INTERVAL,
                                    INTERVAL_LABELS, INTERVALS, candles_of,
                                    period_of)
from ..util import clean_list, gmgn_url, ist_date_str

router = APIRouter(prefix="/api/rsi", tags=["rsi"])

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _clean_address(value) -> str:
    addr = str(value or "").strip()
    if not _ADDRESS_RE.match(addr):
        raise HTTPException(400, f"{value!r} is not a contract address")
    return addr.lower()


def _clean_interval(value, default: str = DEFAULT_INTERVAL,
                    owner: dict | None = None) -> str:
    interval = str(value or default)
    if interval not in INTERVALS:
        raise HTTPException(400, f"unknown interval '{interval}' — "
                                 f"have {', '.join(INTERVALS)}")
    if owner is not None:
        plan = accounts.plan_of(owner)
        if INTERVALS[interval] < plan.min_interval:
            allowed = [k for k in INTERVALS if INTERVALS[k] >= plan.min_interval]
            raise HTTPException(
                402, f"The {plan.label} plan reads "
                     f"{INTERVAL_LABELS[allowed[0]]} and slower. A faster "
                     f"timeframe is a faster bill — it needs a bigger plan.")
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
async def get_settings(owner: dict = Depends(security.account)):
    doc = await _settings_doc(owner)
    enabled = await registry.enabled_map()
    plan = accounts.plan_of(owner)
    return {
        "low": float(doc.get("low", DEFAULT_LOW)),
        "high": float(doc.get("high", DEFAULT_HIGH)),
        "period": int(doc.get("period", DEFAULT_PERIOD)),
        "cadence": str(doc.get("cadence", DEFAULT_CADENCE)),
        # The RSI timeframe a new token starts on — the length of one candle.
        # Not the same thing as `cadence`, which is only how often the reading
        # is recomputed.
        "default_interval": str(doc.get("default_interval", DEFAULT_INTERVAL)),
        # How many candles one reading is made of. Per token as well — this is
        # only what a new one starts on.
        "default_candles": int(doc.get("default_candles", DEFAULT_CANDLES)),
        "candle_choices": list(CANDLE_CHOICES),
        "cadences": list(CADENCES),
        # Only the timeframes this plan may pick: a 1 Sec token is 3,600
        # reads an hour, which is the whole cost of the feature.
        "intervals": [{"id": k, "label": INTERVAL_LABELS[k]}
                      for k in INTERVALS if INTERVALS[k] >= plan.min_interval],
        "min_interval": plan.min_interval,
        "retention_days": scfg.RSI_RETENTION_DAYS,
        "alert_chat_set": bool(scfg.RSI_ALERT_CHAT_ID),
        "enabled": bool(enabled.get("rsi_tracker", True)),
    }


@router.patch("/settings")
async def set_settings(payload: dict = Body(...),
                       owner: dict = Depends(security.require_customer)):
    """Bounds, period and cadence. Whatever is sent is changed; the rest stays."""
    update: dict = {}
    if "low" in payload or "high" in payload:
        current = await _settings_doc(owner)
        low = float(payload.get("low", current.get("low", DEFAULT_LOW)))
        high = float(payload.get("high", current.get("high", DEFAULT_HIGH)))
        if not (0 < low < high < 100):
            raise HTTPException(400, "bounds must be 0 < low < high < 100")
        update["low"], update["high"] = low, high
    if "default_candles" in payload:
        candles = int(payload["default_candles"])
        # Three is the fewest that makes an RSI mean anything; past a few
        # hundred the series is longer than the retention window can hold.
        if not 3 <= candles <= 201:
            raise HTTPException(400, "candles must be between 3 and 201")
        update["default_candles"] = candles
        update["period"] = period_of(candles)
    if "default_interval" in payload:
        update["default_interval"] = _clean_interval(payload["default_interval"],
                                                     owner=owner)
    if "cadence" in payload:
        cadence = str(payload["cadence"])
        if cadence not in CADENCES:
            raise HTTPException(400, f"cadence must be one of {', '.join(CADENCES)}")
        update["cadence"] = cadence
    if not update:
        raise HTTPException(400, "nothing to change")
    await db.get_collection("rsi_settings").update_one(
        {"_id": _settings_id(owner)},
        {"$set": {**update, "user_id": owner["username"]}}, upsert=True)
    return await get_settings(owner)


def _settings_id(owner: dict) -> str:
    return f"rsi:{owner.get('username', '')}"


async def _settings_doc(owner: dict) -> dict:
    return await db.get_collection("rsi_settings").find_one(
        {"_id": _settings_id(owner)}) or {}


@router.get("/tokens")
async def tokens(chain: str = Query("all"), q: str | None = None,
                 date: str | None = None, limit: int = Query(200, le=1000),
                 owner: dict = Depends(security.require_customer)):
    """The panel: every token you added, with its latest reading."""
    flt: dict = {"user_id": owner["username"]}
    if chain != "all":
        flt["chain"] = chain
    rows = await db.get_collection("rsi_tokens").find(flt).sort("added_at", -1).to_list(1000)

    # The price is per token; the reading is per token AND settings. Both are
    # shared with whoever else happens to be watching the same thing.
    prices = {}
    async for st in db.get_collection("rsi_state").find(
            {} if chain == "all" else {"chain": chain}):
        prices[(st.get("chain"), st.get("address"))] = st
    readings = {}
    async for rd in db.get_collection("rsi_readings").find(
            {} if chain == "all" else {"chain": chain}):
        readings[(rd.get("chain"), rd.get("address"), rd.get("interval"),
                  rd.get("period"))] = rd

    fallback_period = period_of(DEFAULT_CANDLES)
    out = []
    for row in rows:
        addr = (row.get("address") or "").lower()
        period = int(row.get("period") or fallback_period)
        st = prices.get((row.get("chain"), addr), {})
        rd = readings.get((row.get("chain"), addr,
                           row.get("interval"), period), {})
        # History: the day is stamped on the reading, so a past day answers
        # "what did this look like then" rather than dropping the row entirely.
        if date and rd.get("day") != date:
            continue
        row = {**row,
               "rsi": rd.get("rsi"), "zone": rd.get("zone") or "",
               "price": st.get("price"), "samples": rd.get("samples") or 0,
               "checked_at": rd.get("checked_at"),
               "updated_at": st.get("updated_at"),
               # Whose alert bookkeeping this is — the row's own, not the
               # shared reading's.
               "last_alert_at": row.get("last_alert_at"),
               "interval_label": INTERVAL_LABELS.get(row.get("interval"), ""),
               "candles": candles_of(period),
               "gmgn_url": gmgn_url(row.get("chain", ""), row.get("address", ""))}
        if q:
            ql = q.lower()
            if not any(ql in str(row.get(f) or "").lower()
                       for f in ("address", "symbol", "name")):
                continue
        out.append(row)
    return {"total": len(out), "items": clean_list(out[:limit])}


@router.post("/tokens")
async def add_token(payload: dict = Body(...),
                    owner: dict = Depends(security.require_customer)):
    """Add a token to watch. Yours alone, and nothing is added on its own."""
    room = await accounts.check_room(owner, "rsi")
    if not room.room:
        raise HTTPException(
            402, f"Your plan tracks {room.limit} tokens and you are tracking "
                 f"{room.used}. Remove one, or move up a plan.")
    chain = str(payload.get("chain") or "").lower()
    if chain not in {**chains(), "sol": None}:
        raise HTTPException(400, f"unknown chain '{chain}'")
    address = _clean_address(payload.get("address"))
    doc = await _settings_doc(owner)
    interval = _clean_interval(payload.get("interval")
                               or doc.get("default_interval") or DEFAULT_INTERVAL,
                               owner=owner)
    symbol = str(payload.get("symbol") or "")[:32]
    name = str(payload.get("name") or "")[:64]
    if not symbol:
        # Read off the contract rather than left blank: a row that says "?" is
        # one you cannot recognise in the panel or in an alert.
        symbol, name = await _name_symbol(chain, address, name)
    now = time.time()
    # The same address may have been tracked on another chain before; its old
    # reading would otherwise show against the new row. Only this account's
    # rows are touched — somebody else may legitimately track it elsewhere.
    await db.get_collection("rsi_tokens").delete_many(
        {"user_id": owner["username"], "address": address,
         "chain": {"$ne": chain}})
    await db.get_collection("rsi_tokens").update_one(
        {"user_id": owner["username"], "chain": chain, "address": address},
        {"$set": {"user_id": owner["username"],
                  "chain": chain, "address": address, "interval": interval,
                  "symbol": symbol,
                  "name": name,
                  "enabled": True, "added_at": now, "day": ist_date_str(now)}},
        upsert=True,
    )
    return {"chain": chain, "address": address, "interval": interval, "added": True}


@router.patch("/tokens/{address}")
async def edit_token(address: str, payload: dict = Body(...),
                     owner: dict = Depends(security.require_customer)):
    """Change one token's own interval, its period, or switch it off."""
    update: dict = {}
    if "interval" in payload:
        update["interval"] = _clean_interval(payload["interval"], owner=owner)
    if "candles" in payload:
        candles = int(payload["candles"])
        if not 3 <= candles <= 201:
            raise HTTPException(400, "candles must be between 3 and 201")
        update["period"] = period_of(candles)
    if "enabled" in payload:
        update["enabled"] = bool(payload["enabled"])
    if not update:
        raise HTTPException(400, "nothing to change")
    addr = _clean_address(address)
    res = await db.get_collection("rsi_tokens").update_one(
        {"user_id": owner["username"], "address": addr}, {"$set": update})
    if not res.matched_count:
        raise HTTPException(404, f"{address} is not being tracked")
    if "interval" in update or "candles" in payload:
        # A new timeframe or candle count is a different question, so what this
        # account has already been told about the old one no longer applies.
        # The candles themselves are NOT deleted: they belong to the timeframe,
        # not to this account, and somebody else may still be reading them.
        await db.get_collection("rsi_tokens").update_one(
            {"user_id": owner["username"], "address": addr},
            {"$set": {"announced_zone": "", "last_alert_at": 0}})
    return {"address": address, **update}


@router.delete("/tokens/{address}")
async def remove_token(address: str,
                       owner: dict = Depends(security.require_customer)):
    addr = _clean_address(address)
    res = await db.get_collection("rsi_tokens").delete_one(
        {"user_id": owner["username"], "address": addr})
    if not res.deleted_count:
        raise HTTPException(404, f"{address} is not being tracked")
    # The candles and the reading stay: they are the token's, not yours, and
    # they age out on their own TTL. Deleting them would blank the chart for
    # everyone else watching the same token.
    return {"address": addr, "removed": True}


async def _name_symbol(chain: str, address: str, name: str = "") -> tuple[str, str]:
    """The token's ticker and name from the chain, or what we were given."""
    import aiohttp
    from ..scanners.rsi_price import PriceReader
    try:
        async with aiohttp.ClientSession() as session:
            got_symbol, got_name = await PriceReader(session).name_symbol(chain, address)
    except Exception:  # noqa: BLE001
        return "", name
    return got_symbol, (name or got_name)


@router.get("/dates")
async def dates(chain: str = Query("all"),
                owner: dict = Depends(security.require_customer)):
    from datetime import datetime
    flt = {} if chain == "all" else {"chain": chain}
    days = [d for d in await db.get_collection("rsi_readings").distinct("day", flt)
            if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"),
                            reverse=True)}


@router.get("/stats")
async def stats(owner: dict = Depends(security.require_customer)):
    """Counted over this account's own list, not the whole database."""
    rows = await db.get_collection("rsi_tokens").find(
        {"user_id": owner["username"]},
        {"_id": 0, "chain": 1, "address": 1, "interval": 1, "period": 1}
    ).to_list(2000)
    fallback = period_of(DEFAULT_CANDLES)
    zones = {"oversold": 0, "overbought": 0}
    for row in rows:
        rd = await db.get_collection("rsi_readings").find_one(
            {"chain": row.get("chain"), "address": row.get("address"),
             "interval": row.get("interval"),
             "period": int(row.get("period") or fallback)},
            {"_id": 0, "zone": 1}) or {}
        if rd.get("zone") in zones:
            zones[rd["zone"]] += 1
    return {
        "total": len(rows),
        "oversold": zones["oversold"],
        "overbought": zones["overbought"],
        "candles": await db.get_collection("rsi_candles").count_documents({}),
        "retention_days": scfg.RSI_RETENTION_DAYS,
    }
