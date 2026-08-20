"""Paper trading: every position this account would have taken, and what it did.

Nothing here spends money. A buy records the price at the moment it was asked
for; a sell records the price at the moment it was closed; the difference is
the answer. That is the whole engine.

It is built this way on purpose rather than as a stepping stone that got left
in. GMGN's trading API signs with the caller's *private key*, not an API key,
and it serves Solana only — while the calls this is meant to follow are mostly
on Robinhood. So the strategy cannot be executed through it today, and the
question worth answering first is whether the strategy is worth executing at
all. This answers that, on every chain, for nothing.

Prices come from DexScreener because it is the one source that covers all five
chains — Robinhood included — and quotes in dollars, which is what a profit and
loss column has to be in. Reads are batched: thirty addresses per request, one
request per refresh, however many positions are open.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import aiohttp

from . import db

# The chains a position may be opened on. Robinhood leads because that is where
# most of the calls land.
CHAINS = ("rbh", "eth", "bnb", "base", "sol")

# DexScreener's own chain ids, which are not ours.
_DS_CHAIN = {"rbh": "robinhood", "eth": "ethereum", "bnb": "bsc",
             "base": "base", "sol": "solana"}

_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/"
_BATCH = 30            # addresses per request, DexScreener's documented ceiling

# What a fresh account starts with. Deliberately small: the first thing anyone
# does is turn auto-buy on and forget it is on.
DEFAULTS: dict[str, Any] = {
    "auto_buy": False,
    # Paper dollars per position. Named the same as the real thing would be, so
    # the number people tune here is the number that would be spent later.
    "buy_usd": 50.0,
    "chains": list(CHAINS),
    # Empty means every starred caller. A list means only these.
    "callers": [],
    "max_open": 20,
    "daily_buys": 20,
    # Held for the day this runs for real. Stored, shown, and not yet used —
    # said plainly in the UI rather than implied by a disabled field.
    "buy_slippage": 30.0,
    "sell_slippage": 30.0,
    "buy_gas_gwei": 0.04,
    "sell_gas_gwei": 0.04,
    "sell_presets": [25, 50, 75, 100],
    # An API key can be stored; it cannot trade on its own. See the module
    # docstring — signing needs a private key, and this never asks for one.
    "gmgn_key": "",
}


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _key(chain: str, address: str) -> str:
    """Solana mints are base58 and case-carrying; hex addresses fold."""
    return address if chain == "sol" else (address or "").lower()


# ── settings ────────────────────────────────────────────────────────────────

async def settings_for(user: str) -> dict:
    doc = await db.get_collection("trading_settings").find_one({"user": user})
    out = dict(DEFAULTS)
    if doc:
        out.update({k: v for k, v in doc.items()
                    if k in DEFAULTS and v is not None})
    return out


async def save_settings(user: str, patch: dict) -> dict:
    keep = {k: v for k, v in patch.items() if k in DEFAULTS}
    if keep:
        await db.get_collection("trading_settings").update_one(
            {"user": user}, {"$set": {**keep, "user": user, "dt": _utc()}},
            upsert=True)
    return await settings_for(user)


# ── prices ──────────────────────────────────────────────────────────────────

async def prices(session: aiohttp.ClientSession,
                 wants: Iterable[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """{(chain, address): usd} for as many as can be answered.

    Missing rather than zero when a token cannot be priced: a position marked
    at zero reads as a total loss, which is a very different thing from "no
    quote right now".
    """
    uniq = list({(c, _key(c, a)) for c, a in wants if a})
    found: dict[tuple[str, str], float] = {}
    for i in range(0, len(uniq), _BATCH):
        chunk = uniq[i:i + _BATCH]
        url = _TOKENS_URL + ",".join(a for _c, a in chunk)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                body = await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        # One address can appear on several pairs and several chains. Take the
        # deepest pool on the chain that was asked about — the thinnest pool on
        # a token is where the misleading price lives.
        best: dict[tuple[str, str], tuple[float, float]] = {}
        for p in (body or {}).get("pairs") or []:
            base = (p.get("baseToken") or {}).get("address") or ""
            chain_id = p.get("chainId") or ""
            usd = p.get("priceUsd")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if not base or not usd:
                continue
            for c, a in chunk:
                if _DS_CHAIN.get(c) == chain_id and _key(c, base) == a:
                    prev = best.get((c, a))
                    if prev is None or float(liq) > prev[1]:
                        best[(c, a)] = (float(usd), float(liq))
        found.update({k: v[0] for k, v in best.items()})
    return found


# ── positions ───────────────────────────────────────────────────────────────

async def open_position(*, user: str, chain: str, address: str, symbol: str = "",
                        name: str = "", usd: float = 0.0, source: str = "manual",
                        caller: str = "", session: aiohttp.ClientSession | None = None,
                        demo_price: float | None = None) -> dict:
    """Record a buy at the price it would have paid right now.

    Refuses rather than guesses when there is no price: a paper trade opened at
    an invented entry is worse than no trade, because it will be averaged in
    with the real ones and quietly move the answer.
    """
    chain = (chain or "").lower()
    if chain not in CHAINS:
        raise ValueError(f"{chain or '?'} is not a chain this trades on")
    addr = _key(chain, address)
    if not addr:
        raise ValueError("no token address")

    conf = await settings_for(user)
    spend = float(usd or conf["buy_usd"])

    price = demo_price
    if price is None:
        own = session is None
        session = session or aiohttp.ClientSession()
        try:
            price = (await prices(session, [(chain, addr)])).get((chain, addr))
        finally:
            if own:
                await session.close()
    if not price or price <= 0:
        raise ValueError("no price for that token right now — nothing to record")

    now = time.time()
    doc = {
        "user": user, "chain": chain, "address": addr,
        "symbol": symbol or "", "name": name or "",
        "usd": spend, "entry": float(price), "qty": spend / float(price),
        "source": source, "caller": caller or "",
        "status": "open", "opened_at": now,
        "last_price": float(price), "last_at": now,
        "dt": _utc(),
    }
    res = await db.get_collection("trading_positions").insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def close_position(user: str, pid: str, *, part: float = 100.0,
                         session: aiohttp.ClientSession | None = None) -> dict:
    """Record a sell of `part` percent of a position, at the current price."""
    from bson import ObjectId
    col = db.get_collection("trading_positions")
    try:
        row = await col.find_one({"_id": ObjectId(pid), "user": user})
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        raise ValueError("no such position")
    if row.get("status") != "open":
        raise ValueError("that position is already closed")

    chain, addr = row["chain"], row["address"]
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        price = (await prices(session, [(chain, addr)])).get((chain, addr))
    finally:
        if own:
            await session.close()
    if not price or price <= 0:
        raise ValueError("no price for that token right now — nothing to record")

    part = max(1.0, min(100.0, float(part)))
    now = time.time()
    qty = float(row["qty"]) * part / 100.0
    out = qty * float(price)
    cost = float(row["usd"]) * part / 100.0

    if part >= 100.0:
        await col.update_one({"_id": row["_id"]}, {"$set": {
            "status": "closed", "exit": float(price), "closed_at": now,
            "last_price": float(price), "last_at": now,
            "pnl_usd": out - cost,
            "pnl_pct": (out - cost) / cost * 100 if cost else 0.0}})
    else:
        # A partial sell leaves the rest running and banks what was taken, so
        # the realised figure is not lost when the remainder is closed later.
        await col.update_one({"_id": row["_id"]}, {"$set": {
            "qty": float(row["qty"]) - qty, "usd": float(row["usd"]) - cost,
            "last_price": float(price), "last_at": now,
            "realised_usd": float(row.get("realised_usd") or 0) + (out - cost)}})
    return await col.find_one({"_id": row["_id"]})


async def refresh(user: str, session: aiohttp.ClientSession | None = None) -> int:
    """Mark every open position to market. Returns how many were priced."""
    col = db.get_collection("trading_positions")
    rows = await col.find({"user": user, "status": "open"}).to_list(500)
    if not rows:
        return 0
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        quotes = await prices(session, [(r["chain"], r["address"]) for r in rows])
    finally:
        if own:
            await session.close()
    now = time.time()
    n = 0
    for r in rows:
        usd = quotes.get((r["chain"], r["address"]))
        if not usd:
            continue
        await col.update_one({"_id": r["_id"]},
                             {"$set": {"last_price": usd, "last_at": now}})
        n += 1
    return n


def view(row: dict) -> dict:
    """One position, with the arithmetic done — the page should not do it."""
    qty, cost = float(row.get("qty") or 0), float(row.get("usd") or 0)
    last = float(row.get("last_price") or 0)
    now_usd = qty * last
    if row.get("status") == "closed":
        pnl = float(row.get("pnl_usd") or 0)
        pct = float(row.get("pnl_pct") or 0)
    else:
        pnl = now_usd - cost
        pct = (pnl / cost * 100) if cost else 0.0
    return {
        "id": str(row.get("_id")),
        "chain": row.get("chain"), "address": row.get("address"),
        "symbol": row.get("symbol") or "", "name": row.get("name") or "",
        "usd": cost, "qty": qty,
        "entry": row.get("entry"), "last": last, "exit": row.get("exit"),
        "pnl_usd": pnl, "pnl_pct": pct,
        "realised_usd": row.get("realised_usd") or 0,
        "status": row.get("status"), "source": row.get("source") or "",
        "caller": row.get("caller") or "",
        "opened_at": row.get("opened_at"), "closed_at": row.get("closed_at"),
        "last_at": row.get("last_at"),
    }


async def can_auto_buy(user: str, chain: str, caller_id: Optional[int],
                       conf: dict | None = None) -> tuple[bool, str]:
    """Whether an automatic buy is allowed right now, and why not when it is not.

    The reason is returned rather than logged because "why did it not buy" is
    the question this feature will be asked most, and an answer that only exists
    in a log file is not an answer.
    """
    conf = conf or await settings_for(user)
    if not conf.get("auto_buy"):
        return False, "auto-buy is off"
    if chain not in (conf.get("chains") or CHAINS):
        return False, f"{chain.upper()} is not in the chain list"
    allow = conf.get("callers") or []
    if allow and caller_id is not None and int(caller_id) not in [int(x) for x in allow]:
        return False, "that caller is not on the list"

    col = db.get_collection("trading_positions")
    if await col.count_documents({"user": user, "status": "open"}) >= int(conf["max_open"]):
        return False, f"already holding {conf['max_open']} positions"
    since = time.time() - 86400
    if await col.count_documents({"user": user, "opened_at": {"$gte": since},
                                  "source": "auto"}) >= int(conf["daily_buys"]):
        return False, "the daily automatic-buy limit is used up"
    return True, ""


# ── the automatic side ──────────────────────────────────────────────────────

async def _starred(chat_id: int) -> bool:
    """Is this caller one of the starred (Important Caller) groups?

    The same list the sound alert uses, and for the same reason: those are the
    groups already judged worth interrupting for, so they are the only ones
    worth acting on. Reading it here rather than keeping a second list means
    starring a caller arms it everywhere at once.
    """
    if chat_id is None:
        return False
    ids = [int(chat_id), -int(chat_id)]
    try:
        ids.append(int(f"-100{int(chat_id)}"))
    except (TypeError, ValueError):
        pass
    row = await db.get_collection("premium_groups").find_one(
        {"id": {"$in": ids}, "ic": True}, {"id": 1})
    return bool(row)


async def on_call(*, chain: str, address: str, symbol: str = "", name: str = "",
                  chat_id: Optional[int] = None, group: str = "") -> list[dict]:
    """A starred caller named a token. Record the buy for whoever asked for it.

    Returns what was opened, which is only used by the tests and the logs — the
    caller of this must not depend on it, and must not fail because of it.
    """
    chain = (chain or "").lower()
    if chain not in CHAINS or not address:
        return []
    if not await _starred(chat_id):
        return []

    armed = await db.get_collection("trading_settings").find(
        {"auto_buy": True}).to_list(500)
    if not armed:
        return []

    made: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for conf_row in armed:
            user = conf_row.get("user")
            if not user:
                continue
            conf = await settings_for(user)
            ok, _why = await can_auto_buy(user, chain, chat_id, conf)
            if not ok:
                continue
            # One position per token per account. A token called by four
            # groups in ten minutes is one idea, not four buys.
            held = await db.get_collection("trading_positions").find_one(
                {"user": user, "chain": chain, "address": _key(chain, address),
                 "status": "open"})
            if held:
                continue
            try:
                row = await open_position(
                    user=user, chain=chain, address=address, symbol=symbol,
                    name=name, usd=float(conf["buy_usd"]), source="auto",
                    caller=group or str(chat_id or ""), session=session)
                made.append(view(row))
            except ValueError:
                # No price yet — a token minutes old often has none. Skipped
                # rather than opened at a guess.
                continue
    return made
