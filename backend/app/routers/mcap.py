"""Market Cap Alert — the panel, the token list, and the target behind each.

The same arrangement as the RSI routes next door: everything here writes to the
collections the worker reads (`mcap_tokens`, `mcap_settings`, `mcap_state`), so
the page, the Telegram screen and the Settings switches cannot drift apart —
there is one copy of the answer.

Nothing is added on its own and nothing is dropped on its own either: a token
sits here until you remove it, target and all, which is the difference between
this and every other panel in the app.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, registry
from ..scanners import scfg
from ..scanners.mcap_price import CHAIN_LABELS, chains
from ..scanners.mcap_tracker import (CADENCES, DEFAULT_CADENCE, armed_for,
                                     first_look, parse_usd)
from ..util import clean_list, gmgn_url, ist_date_str

router = APIRouter(prefix="/api/mcap", tags=["mcap"])

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Base58 without 0 O I l — a Solana mint.
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _clean_address(value, chain: str) -> str:
    """An address in the shape that chain actually uses.

    Case matters on Solana — a mint is base58 and lowercasing it makes it a
    different address — so only the EVM ones are folded.
    """
    addr = str(value or "").strip()
    if chain == "sol":
        if not _SOL_RE.match(addr):
            raise HTTPException(400, f"{value!r} is not a Solana mint address")
        return addr
    if not _EVM_RE.match(addr):
        raise HTTPException(400, f"{value!r} is not a contract address")
    return addr.lower()


def _clean_target(value) -> float:
    try:
        return parse_usd(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/chains")
async def mcap_chains():
    """The filter tabs, and whether each chain is actually switched on."""
    enabled = await registry.enabled_map()
    return {"items": [
        {"id": key, "label": label,
         "enabled": bool(enabled.get(f"mcap_chain_{key}", True))
                    and bool(enabled.get(f"mcap_rpc_{key}", True)),
         "own_endpoints": bool(scfg.MCAP_ENDPOINTS.get(key))}
        for key, label in CHAIN_LABELS.items()
    ]}


@router.get("/settings")
async def get_settings():
    doc = await db.get_collection("mcap_settings").find_one({"_id": "mcap"}) or {}
    enabled = await registry.enabled_map()
    return {
        "cadence": str(doc.get("cadence", DEFAULT_CADENCE)),
        "cadences": list(CADENCES),
        "alert_chat_set": bool(scfg.MCAP_ALERT_CHAT_ID),
        "enabled": bool(enabled.get("mcap_tracker", True)),
    }


@router.patch("/settings")
async def set_settings(payload: dict = Body(...)):
    if "cadence" not in payload:
        raise HTTPException(400, "nothing to change")
    cadence = str(payload["cadence"])
    if cadence not in CADENCES:
        raise HTTPException(400, f"cadence must be one of {', '.join(CADENCES)}")
    await db.get_collection("mcap_settings").update_one(
        {"_id": "mcap"}, {"$set": {"cadence": cadence}}, upsert=True)
    return await get_settings()


@router.get("/tokens")
async def tokens(chain: str = Query("all"), q: str | None = None,
                 date: str | None = None, limit: int = Query(200, le=1000)):
    """Every token you added, with its latest market cap and its target."""
    flt: dict = {} if chain == "all" else {"chain": chain}
    rows = await db.get_collection("mcap_tokens").find(flt) \
                   .sort("added_at", -1).to_list(1000)

    states: dict = {}
    async for st in db.get_collection("mcap_state").find(
            {} if chain == "all" else {"chain": chain}):
        states[(st.get("chain"), st.get("address"))] = st

    out = []
    for row in rows:
        st = states.get((row.get("chain"), row.get("address")), {})
        if date and st.get("day") != date:
            continue
        mcap = st.get("mcap")
        target = float(row.get("target") or 0)
        row = {**row,
               "mcap": mcap, "price_usd": st.get("price_usd"),
               "supply": st.get("supply"), "source": st.get("source") or "",
               "checked_at": st.get("checked_at"),
               # How far it still has to travel, as a percentage — the column
               # you actually watch when a target is set.
               "to_target_pct": (round((target - mcap) / mcap * 100, 1)
                                 if mcap and target else None),
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
    """Watch a token for a market cap. The target is the whole point, so it is
    required — a row with no target would just be a price ticker."""
    chain = str(payload.get("chain") or "").lower()
    if chain not in CHAIN_LABELS:
        raise HTTPException(400, f"unknown chain '{chain}'")
    address = _clean_address(payload.get("address"), chain)
    target = _clean_target(payload.get("target"))
    # One live read at add time, which both names the token and answers "where
    # is it now" — the question the direction depends on. Without it a token
    # nobody has checked yet has no market cap on file, every target reads as
    # "on the way up", and a target set below where it already trades fires on
    # the very first pass.
    symbol, name, current = await first_look(chain, address,
                                             str(payload.get("symbol") or "")[:32],
                                             str(payload.get("name") or "")[:64])
    now = time.time()
    armed = armed_for(target, current)
    await db.get_collection("mcap_tokens").update_one(
        {"chain": chain, "address": address},
        {"$set": {"chain": chain, "address": address, "target": target,
                  "armed": armed, "symbol": symbol, "name": name,
                  "enabled": True, "added_at": now, "day": ist_date_str(now)},
         # A re-added token starts armed again rather than inheriting the hit
         # it fired for its previous target.
         "$unset": {"hit_at": "", "hit_mcap": ""}},
        upsert=True,
    )
    return {"chain": chain, "address": address, "target": target, "armed": armed}


@router.patch("/tokens/{address}")
async def edit_token(address: str, payload: dict = Body(...)):
    """Change the target, or switch this one off without losing it."""
    row = await db.get_collection("mcap_tokens").find_one({"address": address})
    if row is None:
        raise HTTPException(404, f"{address} is not being watched")
    update: dict = {}
    unset: dict = {}
    if "target" in payload:
        target = _clean_target(payload["target"])
        update["target"] = target
        # A new target is a new question, so it re-arms — including which way
        # the market cap has to travel to answer it.
        update["armed"] = await _arm(row.get("chain", ""), row.get("address", ""),
                                     target)
        unset = {"hit_at": "", "hit_mcap": ""}
    if "enabled" in payload:
        update["enabled"] = bool(payload["enabled"])
    if not update:
        raise HTTPException(400, "nothing to change")
    ops: dict = {"$set": update}
    if unset:
        ops["$unset"] = unset
    await db.get_collection("mcap_tokens").update_one({"address": address}, ops)
    return {"address": address, **update}


@router.delete("/tokens/{address}")
async def remove_token(address: str):
    res = await db.get_collection("mcap_tokens").delete_one({"address": address})
    if not res.deleted_count:
        raise HTTPException(404, f"{address} is not being watched")
    await db.get_collection("mcap_state").delete_many({"address": address})
    return {"address": address, "removed": True}


async def _arm(chain: str, address: str, target: float) -> str:
    """Which way an existing token has to move for a new target to mean
    anything, read off the market cap already on file. A token being added has
    no file yet — that path takes its number from `first_look`."""
    st = await db.get_collection("mcap_state").find_one(
        {"chain": chain, "address": address}) or {}
    return armed_for(target, float(st.get("mcap") or 0))


@router.get("/dates")
async def dates(chain: str = Query("all")):
    from datetime import datetime
    flt = {} if chain == "all" else {"chain": chain}
    days = [d for d in await db.get_collection("mcap_state").distinct("day", flt) if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"),
                            reverse=True)}


@router.get("/stats")
async def stats():
    col = db.get_collection("mcap_tokens")
    return {
        "total": await col.count_documents({}),
        "armed": await col.count_documents({"hit_at": {"$exists": False},
                                            "target": {"$gt": 0}}),
        "hit": await col.count_documents({"hit_at": {"$exists": True}}),
        "chains": len([c for c in chains()]) + 1,
    }
