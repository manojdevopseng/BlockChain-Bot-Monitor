"""Market Cap Alert — the panel, the token list, and the target behind each.

The same arrangement as the RSI routes next door: everything here writes to the
collections the worker reads (`mcap_tokens`, `mcap_settings`, `mcap_state`), so
the page, the Telegram screen and the Settings switches cannot drift apart —
there is one copy of the answer.

Nothing is added on its own and nothing is dropped on its own either: a token
sits here until you remove it, target and all, which is the difference between
this and every other panel in the app.

Every row belongs to somebody. `user_id` is the account's username and every
query here carries it, so one account cannot see, edit or delete another's
list — the filter is in the query rather than in the response, because a
filtered response is one forgotten line away from leaking the lot.

The reading itself (`mcap_state`) is deliberately NOT per account: a market cap
is a fact about a token, so twenty accounts watching the same one share a
single read and a single row.
"""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import accounts, db, registry, security
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
async def get_settings(owner: dict = Depends(security.account)):
    doc = await _settings_doc(owner)
    enabled = await registry.enabled_map()
    plan = accounts.plan_of(owner)
    return {
        "cadence": str(doc.get("cadence", _default_cadence(owner))),
        # Only the cadences this plan may pick. A trial sitting on 15s would
        # cost exactly what a paid account costs.
        "cadences": [c for c, secs in CADENCES.items()
                     if secs >= plan.min_cadence],
        "alert_chat_set": bool(scfg.MCAP_ALERT_CHAT_ID),
        "enabled": bool(enabled.get("mcap_tracker", True)),
    }


@router.patch("/settings")
async def set_settings(payload: dict = Body(...),
                       owner: dict = Depends(security.require_customer)):
    if "cadence" not in payload:
        raise HTTPException(400, "nothing to change")
    cadence = str(payload["cadence"])
    if cadence not in CADENCES:
        raise HTTPException(400, f"cadence must be one of {', '.join(CADENCES)}")
    plan = accounts.plan_of(owner)
    if CADENCES[cadence] < plan.min_cadence:
        raise HTTPException(
            402, f"The {plan.label} plan checks every {plan.min_cadence}s at "
                 f"the fastest. A paid plan checks every 15s.")
    await db.get_collection("mcap_settings").update_one(
        {"_id": _settings_id(owner)},
        {"$set": {"cadence": cadence, "user_id": owner["username"]}},
        upsert=True)
    # Every row this account owns moves with it, because the worker reads the
    # cadence off the row rather than looking the account up mid-pass.
    await db.get_collection("mcap_tokens").update_many(
        {"user_id": owner["username"]},
        {"$set": {"cadence": CADENCES[cadence]}})
    return await get_settings(owner)


def _settings_id(owner: dict) -> str:
    return f"mcap:{owner.get('username', '')}"


async def _settings_doc(owner: dict) -> dict:
    return await db.get_collection("mcap_settings").find_one(
        {"_id": _settings_id(owner)}) or {}


def _default_cadence(owner: dict) -> str:
    """The fastest cadence this plan may have, as its starting point."""
    plan = accounts.plan_of(owner)
    for name, secs in CADENCES.items():
        if secs >= plan.min_cadence:
            return name
    return DEFAULT_CADENCE


def _cadence_for(owner: dict) -> int:
    """Seconds between checks for a row this account owns."""
    plan = accounts.plan_of(owner)
    return max(CADENCES[DEFAULT_CADENCE], plan.min_cadence)


@router.post("/check")
async def check(payload: dict = Body(...),
                owner: dict = Depends(security.require_customer)):
    """One market cap, right now, for an address you paste in.

    Nothing is stored and nothing is watched: this is the "what is it worth"
    question on its own, answered from the same reader the watcher uses, so the
    two can never disagree about a number.
    """
    enabled = await registry.enabled_map()
    if not enabled.get("mcap_checker", True):
        raise HTTPException(409, "Market Cap Check is switched off in Settings")
    plan = accounts.plan_of(owner)
    used = await accounts.checks_today(owner["username"])
    if used >= plan.mcap_checks_per_day:
        raise HTTPException(
            402, f"That is {used} checks today — the {plan.label} plan allows "
                 f"{plan.mcap_checks_per_day} a day.")
    chain = str(payload.get("chain") or "").lower()
    if chain not in CHAIN_LABELS:
        raise HTTPException(400, f"unknown chain '{chain}'")
    if not enabled.get(f"mcap_chain_{chain}", True) or \
            not enabled.get(f"mcap_rpc_{chain}", True):
        raise HTTPException(409, f"{CHAIN_LABELS[chain]} is switched off for "
                                 f"Market Cap")
    address = _clean_address(payload.get("address"), chain)

    import aiohttp
    from ..scanners.mcap_price import MarketCapReader
    async with aiohttp.ClientSession() as session:
        reader = MarketCapReader(session)
        symbol, name = await reader.name_symbol(chain, address)
        reading = await reader.read(chain, address)
    if reading is None:
        # An honest "cannot" rather than a zero: no pool yet, or the endpoint
        # is refusing. Both are worth saying out loud.
        raise HTTPException(404, f"no price found for {address} on "
                                 f"{CHAIN_LABELS[chain]} — it may have no pool yet")
    # Counted after the answer, so a lookup that could not be read costs
    # nothing against the allowance.
    used_now = await accounts.note_check(owner["username"])
    return {"checks_today": used_now, "checks_allowed": plan.mcap_checks_per_day,
            "chain": chain, "chain_label": CHAIN_LABELS[chain], "address": address,
            "symbol": symbol, "name": name, "mcap": reading.mcap,
            "price_usd": reading.price_usd, "price_native": reading.price_native,
            "supply": reading.supply, "source": reading.source,
            "checked_at": time.time(),
            "gmgn_url": gmgn_url(chain, address)}


@router.get("/tokens")
async def tokens(chain: str = Query("all"), q: str | None = None,
                 date: str | None = None, limit: int = Query(200, le=1000),
                 owner: dict = Depends(security.require_customer)):
    """Every token you added, with its latest market cap and its target."""
    flt: dict = {"user_id": owner["username"]}
    if chain != "all":
        flt["chain"] = chain
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
async def add_token(payload: dict = Body(...),
                    owner: dict = Depends(security.require_customer)):
    """Watch a token for a market cap. The target is the whole point, so it is
    required — a row with no target would just be a price ticker."""
    room = await accounts.check_room(owner, "mcap")
    if not room.room:
        raise HTTPException(
            402, f"Your plan watches {room.limit} tokens and you are watching "
                 f"{room.used}. Remove one, or move up a plan.")
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
        {"user_id": owner["username"], "chain": chain, "address": address},
        {"$set": {"user_id": owner["username"],
                  "chain": chain, "address": address, "target": target,
                  "armed": armed, "symbol": symbol, "name": name,
                  # Read off the row by the worker, so a pass never has to look
                  # an account up. Floored by what the plan allows.
                  "cadence": _cadence_for(owner),
                  "enabled": True, "added_at": now, "day": ist_date_str(now)},
         # A re-added token starts armed again rather than inheriting the hit
         # it fired for its previous target.
         "$unset": {"hit_at": "", "hit_mcap": ""}},
        upsert=True,
    )
    return {"chain": chain, "address": address, "target": target, "armed": armed}


@router.patch("/tokens/{address}")
async def edit_token(address: str, payload: dict = Body(...),
                     owner: dict = Depends(security.require_customer)):
    """Change the target, or switch this one off without losing it."""
    row = await db.get_collection("mcap_tokens").find_one(
        {"user_id": owner["username"], "address": address})
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
    await db.get_collection("mcap_tokens").update_one(
        {"user_id": owner["username"], "address": address}, ops)
    return {"address": address, **update}


@router.delete("/tokens/{address}")
async def remove_token(address: str,
                       owner: dict = Depends(security.require_customer)):
    res = await db.get_collection("mcap_tokens").delete_one(
        {"user_id": owner["username"], "address": address})
    if not res.deleted_count:
        raise HTTPException(404, f"{address} is not being watched")
    # The reading stays: somebody else may still be watching this token, and
    # nobody paying for it costs one small document until it is read again.
    return {"address": address, "removed": True}


async def _arm(chain: str, address: str, target: float) -> str:
    """Which way an existing token has to move for a new target to mean
    anything, read off the market cap already on file. A token being added has
    no file yet — that path takes its number from `first_look`."""
    st = await db.get_collection("mcap_state").find_one(
        {"chain": chain, "address": address}) or {}
    return armed_for(target, float(st.get("mcap") or 0))


@router.get("/dates")
async def dates(chain: str = Query("all"),
                owner: dict = Depends(security.require_customer)):
    from datetime import datetime
    flt: dict = {} if chain == "all" else {"chain": chain}
    days = [d for d in await db.get_collection("mcap_state").distinct("day", flt) if d]
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"),
                            reverse=True)}


@router.get("/stats")
async def stats(owner: dict = Depends(security.require_customer)):
    col = db.get_collection("mcap_tokens")
    mine = {"user_id": owner["username"]}
    return {
        "total": await col.count_documents(mine),
        "armed": await col.count_documents({**mine, "hit_at": {"$exists": False},
                                            "target": {"$gt": 0}}),
        "hit": await col.count_documents({**mine, "hit_at": {"$exists": True}}),
        "chains": len([c for c in chains()]) + 1,
    }
