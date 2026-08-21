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

import asyncio
import time
from datetime import datetime, timedelta, timezone
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
    # Empty means every starred caller. A list means only these — and it can
    # only ever narrow that set, never widen it: a caller outside the starred
    # list is not reachable from here at all.
    "callers": [],
    # Gas-fee detections, armed separately from callers. Off by default and
    # kept apart on purpose: a caller's token has a person behind it, a gas
    # token has nobody, so arming one must never quietly arm the other. The
    # master auto_buy switch still gates it, which is what makes the kill
    # switch stop both.
    "auto_buy_gas": False,
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

    # ── the sellability guard ──
    # Asked only of gas-fee tokens, and only of them on purpose: they are
    # minutes old, no human has vouched for them, and they are where the
    # honeypots live. A token a caller named has a person behind it and a
    # history to read, so it is not put through this.
    "sell_check": True,
    "sell_check_min_sells": 3,

    # ── auto-sell ──
    "auto_sell": False,
    "take_profit_pct": 100.0,
    "stop_loss_pct": 50.0,
    # 0 turns the trailing stop off. It arms only after a position has actually
    # been in profit, so it can never fire on the way up from the entry.
    "trailing_pct": 0.0,

    # ── the daily loss limit ──
    "loss_limit_on": False,
    "loss_limit_pct": 20.0,

    # Why auto-buy last went off — the kill switch, or the loss limit tripping.
    # Kept so the page can say which, rather than showing a toggle that is
    # mysteriously off and leaving the reason in a log file.
    "stopped_reason": "",
    "stopped_at": 0.0,

    # Telegram. Where it goes is not a setting — an account that connected its
    # own chat gets its own trades there and nobody else's; see
    # telegram_link.alert_target.
    "tg_alerts": True,
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
    # Switching auto-buy back on is the acknowledgement: the banner explaining
    # why it stopped has been read, and should not outlive the stop.
    if keep.get("auto_buy") and "stopped_reason" not in keep:
        keep["stopped_reason"] = ""
        keep["stopped_at"] = 0.0
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
                        caller: str = "", caller_id: Optional[int] = None,
                        session: aiohttp.ClientSession | None = None,
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

    # Before buying a gas-fee token, ask whether anyone has managed to sell it.
    # Refused rather than warned about: the whole value of the check is that it
    # happens before the money moves, and a warning nobody reads is not a check.
    if source != "demo" and conf.get("sell_check") and await is_gas_token(chain, addr):
        own = session is None
        session = session or aiohttp.ClientSession()
        try:
            ok, why = await sellable(
                session, chain, addr,
                min_sells=int(conf.get("sell_check_min_sells") or 3))
        finally:
            if own:
                await session.close()
                session = None
        if not ok:
            raise ValueError(why)

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
        # The id is what the P&L groups on; the name is only what it was called
        # on the day. A group renamed on Telegram used to split its own history
        # into two rows that looked like two different callers.
        "caller_id": int(caller_id) if caller_id is not None else None,
        "status": "open", "opened_at": now,
        "last_price": float(price), "last_at": now,
        # The high-water mark a trailing stop measures its drop from. Seeded at
        # the entry so a position that only ever falls has nothing to trail.
        "peak_price": float(price),
        "dt": _utc(),
    }
    res = await db.get_collection("trading_positions").insert_one(doc)
    doc["_id"] = res.inserted_id
    notify_open(doc)
    return doc


async def close_position(user: str, pid: str, *, part: float = 100.0,
                         session: aiohttp.ClientSession | None = None,
                         reason: str = "manual") -> dict:
    """Record a sell of `part` percent of a position, at the current price.

    `reason` is what closed it — "manual", or the rule that fired. Stored on the
    row so the table can say why a position the account never touched is gone.
    """
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
            "closed_reason": reason,
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
    done = await col.find_one({"_id": row["_id"]})
    notify_close(done, part=part, reason=reason)
    return done


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
        "peak": row.get("peak_price") or row.get("entry"),
        "closed_reason": row.get("closed_reason") or "",
        "status": row.get("status"), "source": row.get("source") or "",
        "caller": row.get("caller") or "",
        "caller_id": row.get("caller_id"),
        "opened_at": row.get("opened_at"), "closed_at": row.get("closed_at"),
        "last_at": row.get("last_at"),
    }


async def can_auto_buy(user: str, chain: str, caller_id: Optional[int],
                       conf: dict | None = None,
                       source: str = "auto") -> tuple[bool, str]:
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
    if source == "gas":
        if not conf.get("auto_buy_gas"):
            return False, "gas-fee auto-buy is off"
    else:
        allow = conf.get("callers") or []
        if allow and caller_id is not None and int(caller_id) not in [int(x) for x in allow]:
            return False, "that caller is not on the list"

    col = db.get_collection("trading_positions")
    if await col.count_documents({"user": user, "status": "open"}) >= int(conf["max_open"]):
        return False, f"already holding {conf['max_open']} positions"
    if await col.count_documents(
            {"user": user, "opened_at": {"$gte": _day_start()},
             "source": {"$in": ["auto", "gas"]}}) >= int(conf["daily_buys"]):
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
                    caller=group or str(chat_id or ""), caller_id=chat_id,
                    session=session)
                made.append(view(row))
            except ValueError:
                # No price yet — a token minutes old often has none. Skipped
                # rather than opened at a guess.
                continue
    return made


# ── the sellability guard ───────────────────────────────────────────────────

async def is_gas_token(chain: str, address: str) -> bool:
    """Did this token reach us as an ETH gas-fee detection?

    The guard below costs a request on the critical path of a buy, so it is
    worth asking only where it earns its keep. Addresses in `gas_alerts` are
    written lowercase by the on-chain detector, and `_key` has already
    lowercased this one, so the comparison is a plain equality.
    """
    if chain != "eth" or not address:
        return False
    try:
        row = await db.get_collection("gas_alerts").find_one(
            {"address": address}, {"_id": 1})
    except Exception:  # noqa: BLE001
        return False
    return bool(row)


async def sellable(session: aiohttp.ClientSession, chain: str, address: str, *,
                   min_sells: int = 3) -> tuple[bool, str]:
    """Has anybody actually got out of this token?

    This is not a swap simulation and does not pretend to be one. It reads the
    pool's own buy and sell counts: a token with forty buys and not one sell is
    either seconds away from its first sell or a honeypot, and at the moment a
    gas alert fires there is no way to tell those two apart from the outside.

    So it refuses, and says why. Nothing is lost by refusing — the token can be
    bought by hand a few minutes later, once the tape has answered the question
    for us. What would be lost by not refusing is the whole position.

    A real simulation needs an `eth_call` against a router with state overrides,
    which our RPC tier does not reliably serve; when it does, this is the one
    function to replace.
    """
    ds = _DS_CHAIN.get(chain)
    if not ds:
        return True, ""
    try:
        async with session.get(_TOKENS_URL + address,
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            body = await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        # A check that cannot run must not quietly become a pass: at this age
        # the token is exactly the kind this exists to stop.
        return False, "could not check whether it can be sold — not buying blind"

    best: Optional[dict] = None
    best_liq = -1.0
    for p in (body or {}).get("pairs") or []:
        if (p.get("chainId") or "") != ds:
            continue
        base = (p.get("baseToken") or {}).get("address") or ""
        if _key(chain, base) != address:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        if liq > best_liq:
            best, best_liq = p, liq
    if not best:
        return False, "no pool for it yet — nothing to sell into"

    txns = best.get("txns") or {}
    buys = sells = 0
    # h24 covers a token minutes old completely; the shorter windows are there
    # for the case where DexScreener has not filled the longer one in yet.
    for window in ("h24", "h6", "h1", "m5"):
        w = txns.get(window) or {}
        if w.get("buys") or w.get("sells"):
            buys, sells = int(w.get("buys") or 0), int(w.get("sells") or 0)
            break

    if best_liq < 1000:
        return False, f"the pool holds only ${best_liq:,.0f} — too thin to get out of"
    if sells >= min_sells:
        return True, ""
    if sells == 0 and buys == 0:
        return False, "no trades on it yet — too early to tell if it can be sold"
    if sells == 0:
        return False, f"{buys} buys and not one sell — nothing has got out of it"
    plural = "sells" if sells != 1 else "sell"
    return False, f"only {sells} {plural} against {buys} buys — not enough to trust"


# ── what the callers are worth ──────────────────────────────────────────────

# The trading day runs on Indian time. On UTC the day rolled over at 05:30
# local, which cut a night's trading in half and reset the loss limit in the
# middle of a session that was still going.
IST = timezone(timedelta(hours=5, minutes=30))


def _day_start() -> float:
    """Midnight IST today, as a unix timestamp."""
    now = datetime.now(IST)
    return datetime(now.year, now.month, now.day, tzinfo=IST).timestamp()


async def day_pnl(user: str) -> dict:
    """Today's damage, against what today put at risk.

    Measured over positions opened or closed since midnight UTC, because a
    limit that counted last week's losses would trip on a day that had lost
    nothing. Demo positions are left out — they are not real decisions and must
    never be able to stop a real one.
    """
    start = _day_start()
    rows = await db.get_collection("trading_positions").find(
        {"user": user, "source": {"$ne": "demo"},
         "$or": [{"opened_at": {"$gte": start}},
                 {"closed_at": {"$gte": start}}]}).to_list(2000)
    cost = pnl = 0.0
    for r in rows:
        v = view(r)
        cost += v["usd"]
        pnl += v["pnl_usd"] + float(v["realised_usd"] or 0)
    return {
        "cost": round(cost, 2),
        "pnl": round(pnl, 2),
        "pct": round(pnl / cost * 100, 2) if cost else 0.0,
        "trades": len(rows),
    }


async def caller_stats(user: str) -> list[dict]:
    """Per caller: what following them has actually been worth.

    The question the whole feature exists to answer — which of these groups is
    paying for itself and which is eating the account. Realised and unrealised
    are kept apart because a caller carried by one position nobody has closed
    yet has not proved anything.
    """
    rows = await db.get_collection("trading_positions").find(
        {"user": user, "source": {"$ne": "demo"}}).to_list(5000)
    # Current names for the ids, so a group renamed on Telegram shows one row
    # under its new name rather than two under both.
    names = {c["id"]: c["name"] for c in await starred_callers()}
    agg: dict[str, dict] = {}
    for r in rows:
        v = view(r)
        cid = v.get("caller_id")
        if cid is not None:
            key = f"id:{cid}"
            who = names.get(int(cid)) or v["caller"] or str(cid)
        elif v["source"] == "gas":
            key = who = "ETH Gas Fees"
        else:
            # Rows from before ids were stored, and every manual buy.
            who = v["caller"] or ("Manual" if v["source"] == "manual" else "Unattributed")
            key = who
        a = agg.setdefault(key, {"caller": who, "trades": 0, "open": 0, "closed": 0,
                                 "wins": 0, "cost": 0.0, "realised": 0.0,
                                 "unrealised": 0.0, "best": None, "worst": None})
        a["trades"] += 1
        a["cost"] += v["usd"]
        banked = float(v["realised_usd"] or 0)
        if v["status"] == "open":
            a["open"] += 1
            a["unrealised"] += v["pnl_usd"]
            a["realised"] += banked
        else:
            a["closed"] += 1
            a["realised"] += v["pnl_usd"] + banked
            if v["pnl_usd"] + banked > 0:
                a["wins"] += 1
        pct = v["pnl_pct"]
        a["best"] = pct if a["best"] is None else max(a["best"], pct)
        a["worst"] = pct if a["worst"] is None else min(a["worst"], pct)
        a["caller"] = who

    out = []
    for a in agg.values():
        a["pnl"] = round(a["realised"] + a["unrealised"], 2)
        a["pct"] = round(a["pnl"] / a["cost"] * 100, 2) if a["cost"] else 0.0
        a["win_rate"] = round(a["wins"] / a["closed"] * 100, 1) if a["closed"] else None
        for k in ("cost", "realised", "unrealised"):
            a[k] = round(a[k], 2)
        for k in ("best", "worst"):
            a[k] = round(a[k], 1) if a[k] is not None else None
        out.append(a)
    out.sort(key=lambda x: x["pnl"], reverse=True)
    return out


async def starred_callers() -> list[dict]:
    """The starred groups, as something to tick in a list.

    Read straight from `premium_groups` rather than kept as a second list, so a
    group starred for the sound alert is one that can be followed here without
    being added twice.
    """
    rows = await db.get_collection("premium_groups").find(
        {"ic": True}, {"id": 1, "name": 1, "username": 1}).to_list(500)
    out = []
    for r in rows:
        gid = r.get("id")
        if gid is None:
            continue
        out.append({"id": int(gid),
                    "name": r.get("name") or str(gid),
                    "username": r.get("username") or ""})
    out.sort(key=lambda x: x["name"].lower())
    return out


# ── stopping ────────────────────────────────────────────────────────────────

async def stop_auto_buy(user: str, reason: str) -> dict:
    """One flip: nothing buys on its own until somebody turns it back on.

    Deliberately does not touch open positions or the auto-sell rules. Stopping
    the buying is the emergency; throwing away the stop-losses on what is
    already held would be a second one.
    """
    out = await save_settings(user, {"auto_buy": False,
                                     "stopped_reason": reason,
                                     "stopped_at": time.time()})
    # Auto-buy going quiet is the kind of thing somebody notices a week later
    # and cannot explain. The page says why once it is opened; this is what
    # gets them to open it.
    try:
        from . import notifications
        await notifications.notify(
            user, notifications.SYSTEM, "Auto-buy stopped", reason, "/trading")
    except Exception:  # noqa: BLE001
        pass
    return out


# ── auto-sell and the loss limit ────────────────────────────────────────────

async def run_rules(user: str, session: aiohttp.ClientSession,
                    conf: dict | None = None) -> dict:
    """Mark this account to market, then act on its own rules.

    Marking happens whether or not auto-sell is on: the P&L column has to be
    true for an account watching it by hand, and the loss limit is measured
    from the same numbers. Only the selling is gated on the toggle.
    """
    conf = conf or await settings_for(user)
    col = db.get_collection("trading_positions")
    rows = await col.find({"user": user, "status": "open"}).to_list(500)

    marked = 0
    sold: list[dict] = []
    if rows:
        quotes = await prices(session, [(r["chain"], r["address"]) for r in rows])
        now = time.time()
        tp = float(conf.get("take_profit_pct") or 0)
        sl = float(conf.get("stop_loss_pct") or 0)
        tr = float(conf.get("trailing_pct") or 0)
        for r in rows:
            usd = quotes.get((r["chain"], r["address"]))
            if not usd:
                continue
            entry = float(r.get("entry") or 0)
            peak = max(float(r.get("peak_price") or entry or 0), usd)
            await col.update_one({"_id": r["_id"]}, {"$set": {
                "last_price": usd, "last_at": now, "peak_price": peak}})
            marked += 1

            if not conf.get("auto_sell") or r.get("source") == "demo" or entry <= 0:
                continue
            pct = (usd - entry) / entry * 100
            why = ""
            if tp > 0 and pct >= tp:
                why = f"take profit +{pct:.0f}%"
            elif sl > 0 and pct <= -sl:
                why = f"stop loss {pct:.0f}%"
            elif tr > 0 and peak > entry and usd <= peak * (1 - tr / 100):
                off = (peak - usd) / peak * 100
                why = f"trailing stop -{off:.0f}% off the peak"
            if not why:
                continue
            try:
                await close_position(user, str(r["_id"]), part=100.0,
                                     session=session, reason=why)
                sold.append({"symbol": r.get("symbol") or r.get("address"),
                             "chain": r.get("chain"), "why": why})
            except ValueError:
                # Unpriceable in the second between marking and closing. It
                # keeps its stop and gets another pass in a minute.
                continue

    day = await day_pnl(user)
    stopped = ""
    limit = abs(float(conf.get("loss_limit_pct") or 0))
    if (conf.get("loss_limit_on") and conf.get("auto_buy")
            and limit > 0 and day["pct"] <= -limit):
        stopped = f"daily loss limit - down {abs(day['pct']):.1f}% today"
        await stop_auto_buy(user, stopped)

    return {"marked": marked, "sold": sold, "day": day, "stopped": stopped}


# ── telling the account what happened ───────────────────────────────────────

# asyncio holds only weak references to tasks, so a notification handed to
# create_task and forgotten can be collected mid-flight. Same bug that lost
# tracker media; same fix.
_bg: set = set()

_TONE = {"buy": "\U0001F7E2", "sell": "\U0001F534"}


async def _notify(user: str, text: str, keyboard: list | None = None) -> None:
    """One line to whoever this account belongs to, and nobody else.

    Where it lands is not a setting on this page. `alert_target` already
    answers it correctly for every case: an account that connected its own
    Telegram gets its trades in its own chat, an admin without one falls back
    to the operator group set in Settings, and anyone else gets nothing —
    because posting one customer's positions into a shared group is a leak,
    not a fallback.
    """
    try:
        from . import alert_dispatch, telegram_link
        from .scanners import scfg
        conf = await settings_for(user)
        if not conf.get("tg_alerts", True):
            return
        chat, _why = await telegram_link.alert_target(
            user, getattr(scfg, "TRADING_ALERT_CHAT_ID", "") or None)
        if not chat:
            return
        await alert_dispatch.send_personal(user, chat, text, keyboard)
    except Exception as exc:  # noqa: BLE001
        from .scanners.slog import get_logger
        get_logger(__name__).debug(f"[TRADING] notify failed: {exc}")


def _notify_bg(user: str, text: str, keyboard: list | None = None) -> None:
    task = asyncio.ensure_future(_notify(user, text, keyboard))
    _bg.add(task)
    task.add_done_callback(_bg.discard)


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def _buttons(row: dict) -> list:
    """The same buttons every other alert carries, plus the page this came from.

    Built by tgstyle so a position notice offers exactly what a detection
    offers — chart first, because that is the reflex — rather than growing its
    own idea of what a token is worth looking at. The mute actions belong to
    the feeds, not to somebody's own position, so they are left off.
    """
    from . import tgstyle
    back = _dash_url("/trading")
    extra = [[{"text": "🖥 Open Trading", "url": back}]] if back else None
    return tgstyle.keyboard(
        chain=row.get("chain") or "", address=row.get("address") or "",
        mute=False, extra=extra)


def _dash_url(path: str) -> str:
    """A link back into the dashboard, or "" when there is nowhere to link to.

    Telegram refuses a message whose button carries an unreachable URL — the
    whole notice fails, not just the button — and PUBLIC_URL ships defaulted to
    localhost. So a deployment that has not said where it lives gets the alert
    without the button rather than no alert at all.
    """
    from .config import settings
    base = (getattr(settings, "public_url", "") or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return ""
    host = base.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or not host:
        return ""
    return f"{base}{path}"


def notify_open(row: dict) -> None:
    """A position was opened. Demo trades stay off Telegram."""
    if row.get("source") == "demo":
        return
    sym = row.get("symbol") or (row.get("address") or "")[:10]
    line = (f"{_TONE['buy']} <b>BUY</b> {sym} · {str(row.get('chain','')).upper()}\n"
            f"${_fmt(float(row.get('usd') or 0))} at {float(row.get('entry') or 0):.8g}")
    who = row.get("caller")
    if who:
        line += f"\nCaller: {who}"
    elif row.get("source") == "gas":
        line += "\nSource: ETH Gas Fees"
    _notify_bg(row.get("user") or "",
               line + "\n\n<i>Paper trade — nothing was sent to a chain.</i>",
               _buttons(row))


def notify_close(row: dict, *, part: float, reason: str) -> None:
    if row.get("source") == "demo":
        return
    sym = row.get("symbol") or (row.get("address") or "")[:10]
    v = view(row)
    pnl = v["pnl_usd"] if v["status"] == "closed" else float(v["realised_usd"] or 0)
    sign = "+" if pnl >= 0 else "-"
    head = f"{_TONE['sell']} <b>SELL</b> {sym} · {str(row.get('chain','')).upper()}"
    if part < 100:
        head += f" ({part:.0f}%)"
    line = (f"{head}\n{sign}${_fmt(abs(pnl))} "
            f"({sign}{abs(v['pnl_pct']):.1f}%)\nClosed by: {reason}")
    _notify_bg(row.get("user") or "",
               line + "\n\n<i>Paper trade — nothing was sent to a chain.</i>",
               _buttons(row))


# ── gas-fee tokens, queued rather than bought on sight ──────────────────────

# A gas alert fires seconds after the pool exists, when nobody has sold the
# token yet — so the sellability guard would refuse every single one of them
# and the feature would be theatre. Instead the buy is queued and retried while
# the tape fills in. If it still cannot be sold by the time this gives up, that
# is the guard doing its job.
GAS_FIRST_TRY = 90        # seconds after detection
GAS_MAX_TRIES = 6
GAS_GIVE_UP = 600         # ten minutes, then the idea is stale anyway


async def on_gas(*, chain: str = "eth", address: str, symbol: str = "",
                 name: str = "") -> int:
    """An ETH gas-fee token was detected. Queue it for every account armed for it.

    Returns how many accounts queued it — used by the logs and the tests only.
    The caller of this must never fail because of it.
    """
    addr = _key(chain, address)
    if not addr:
        return 0
    armed = await db.get_collection("trading_settings").find(
        {"auto_buy": True, "auto_buy_gas": True}).to_list(500)
    if not armed:
        return 0

    now = time.time()
    queued = 0
    col = db.get_collection("trading_pending")
    for conf_row in armed:
        user = conf_row.get("user")
        if not user:
            continue
        conf = await settings_for(user)
        ok, _why = await can_auto_buy(user, chain, None, conf, source="gas")
        if not ok:
            continue
        held = await db.get_collection("trading_positions").find_one(
            {"user": user, "chain": chain, "address": addr, "status": "open"})
        if held:
            continue
        await col.update_one(
            {"user": user, "chain": chain, "address": addr},
            {"$setOnInsert": {
                "user": user, "chain": chain, "address": addr,
                "symbol": symbol or "", "name": name or "",
                "tries": 0, "next_at": now + GAS_FIRST_TRY,
                "expires_at": now + GAS_GIVE_UP, "dt": _utc()}},
            upsert=True)
        queued += 1
    return queued


async def sweep_pending(session: aiohttp.ClientSession) -> dict:
    """Try the queued gas buys whose turn has come. Called once a minute."""
    col = db.get_collection("trading_pending")
    now = time.time()
    rows = await col.find({"next_at": {"$lte": now}}).to_list(200)
    bought, dropped = [], []
    for r in rows:
        if now >= float(r.get("expires_at") or 0):
            await col.delete_one({"_id": r["_id"]})
            dropped.append({"symbol": r.get("symbol") or r["address"],
                            "why": "gave up — never became sellable"})
            continue
        conf = await settings_for(r["user"])
        ok, why = await can_auto_buy(r["user"], r["chain"], None, conf, source="gas")
        if not ok:
            # Auto-buy went off, or the day's cap filled, between queueing and
            # now. Dropped rather than held: the answer will not change back
            # inside the ten minutes this row has left.
            await col.delete_one({"_id": r["_id"]})
            dropped.append({"symbol": r.get("symbol") or r["address"], "why": why})
            continue
        try:
            row = await open_position(
                user=r["user"], chain=r["chain"], address=r["address"],
                symbol=r.get("symbol") or "", name=r.get("name") or "",
                usd=float(conf["buy_usd"]), source="gas", session=session)
            await col.delete_one({"_id": r["_id"]})
            bought.append(view(row))
        except ValueError as exc:
            tries = int(r.get("tries") or 0) + 1
            if tries >= GAS_MAX_TRIES:
                await col.delete_one({"_id": r["_id"]})
                dropped.append({"symbol": r.get("symbol") or r["address"],
                                "why": str(exc)})
            else:
                await col.update_one({"_id": r["_id"]}, {"$set": {
                    "tries": tries, "next_at": now + 60, "last_why": str(exc)}})
    return {"bought": bought, "dropped": dropped}
