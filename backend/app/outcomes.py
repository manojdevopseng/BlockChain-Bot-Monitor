"""Did the alert turn out to be any good?

Every alert this bot fires is recorded and then forgotten. Nobody — including
the person tuning SOL_MIN_MCAP and MIN_FEE_ETH — can say whether the thresholds
are producing winners or noise. This follows each alert forward and records the
price at fixed intervals, so the dashboard can answer "SOL→RBH matches averaged
+18% at 1h" instead of "43 alerts fired".

Two things it is careful about:

  • GMGN pacing. Prices come from the SAME shared GMGNClient the scanners use,
    so every request goes through the one rate limiter. gmgn.ai's Cloudflare
    403s a datacenter IP that polls too fast, and that was hard-won — a second
    client, or a burst of catch-up lookups, would put it back at risk. Checks
    are due-based and capped per cycle.
  • Never blocking a scanner. This runs as its own supervisor task; a failed
    price lookup marks the check and moves on.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from . import db
from .util import esc
from .scanners.slog import get_logger

log = get_logger(__name__)

# When to look, measured from the alert. Keys are what the UI groups by.
CHECKPOINTS: list[tuple[str, int]] = [
    ("15m", 15 * 60),
    ("1h",  60 * 60),
    ("6h",  6 * 3600),
    ("24h", 24 * 3600),
]

# Sources, so returns can be compared per feature rather than lumped together.
SRC_XCHAIN_ETH = "xchain_eth"
SRC_XCHAIN_RBH = "xchain_rbh"
SRC_GAS = "gas"
SRC_PREMIUM = "premium"

# Which checkpoints get posted back to Telegram as a reply to the original
# alert. Not all four: at 63 alerts a day that would be 250 messages against a
# group limit of ~18 a minute. The first real read and the final one are what
# actually tell you whether the call was any good.
REPLY_CHECKPOINTS = ("1h", "24h")

CYCLE_SECONDS = 60          # how often to look for due checks
MAX_PER_CYCLE = 8           # hard cap on GMGN calls per cycle
STALE_AFTER = 36 * 3600     # stop chasing an alert this old


def _col():
    return db.get_collection("outcomes")


async def track(*, source: str, chain: str, address: str, symbol: str,
                groups: Optional[list[str]] = None, **extra: Any) -> None:
    """Start following an alert. Cheap and idempotent — one row per address
    per source, so a token called twice is not counted twice."""
    if not address:
        return
    now = time.time()
    try:
        # A second group calling the same token is added to `groups` rather
        # than starting a new row: the ranking needs to credit both, but the
        # price only needs following once.
        update: dict = {"$setOnInsert": {
                "source": source, "chain": (chain or "").lower(),
                "address": address.lower(), "symbol": symbol,
                "groups": groups or [],
                "created_at": now, "entry_price": None, "entry_at": None,
                "checks": {}, "done": False, **extra,
        }}
        if groups:
            update["$addToSet"] = {"groups": {"$each": groups}}
            update["$setOnInsert"].pop("groups", None)
        await _col().update_one(
            {"source": source, "address": address.lower()}, update, upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[OUTCOME] could not start tracking {symbol}: {exc}")


# Where a price can actually be read, per chain. Measured, not assumed:
#   • DexScreener answers for ethereum and solana, free and without a key, and
#     it is not gmgn.ai — so outcome checks cannot put the Cloudflare fix at
#     risk no matter how many run.
#   • GMGN's token-info endpoint returns an empty body for eth and robinhood
#     (verified against live addresses), so it is only useful for sol.
#   • Robinhood Chain is on neither. Those alerts are tracked but left unpriced
#     rather than reported as 0% — see PRICED_CHAINS below.
_DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{addr}"

_DS_CHAIN = {"eth": "ethereum", "ethereum": "ethereum",
             "sol": "solana", "solana": "solana"}

# Chains an outcome can be measured on at all. Robinhood is not listed on any
# aggregator, so its price is read straight off the pool — see rbh_price.
PRICED_CHAINS = frozenset({"eth", "ethereum", "sol", "solana", "robinhood", "rbh"})
_RBH_CHAINS = frozenset({"robinhood", "rbh"})


def is_priceable(chain: str | None) -> bool:
    return (chain or "").lower() in PRICED_CHAINS


async def _price(client, chain: str, address: str,
                 doc: Optional[dict] = None) -> Optional[float]:
    """Current USD price, or None if this chain has no source.

    `client` is the shared GMGNClient, used only for Solana. Robinhood is read
    from its own pool over RBH_RPC_HTTP; everything else goes to DexScreener
    over a short-lived session.
    """
    c = (chain or "").lower()
    if c in _RBH_CHAINS:
        from . import rbh_price
        doc = doc or {}
        return await rbh_price.price_usd(
            token=address, dex=doc.get("dex") or "", pair=doc.get("pair"),
            pool_id=doc.get("pool_id"), weth_is_token0=doc.get("weth_is_token0"),
        )

    want = _DS_CHAIN.get(c)
    if not want:
        return None

    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_DEXSCREENER.format(addr=address),
                             timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[OUTCOME] price lookup failed for {address[:10]}: {exc}")
        return None

    # Several pairs can quote the same token; take the deepest, which is the
    # one a trade would actually touch.
    pairs = [p for p in (data.get("pairs") or [])
             if (p.get("chainId") or "").lower() == want and p.get("priceUsd")]
    if not pairs:
        return None
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    try:
        return float(pairs[0]["priceUsd"]) or None
    except (TypeError, ValueError):
        return None


def _due(doc: dict, now: float) -> Optional[tuple[str, int]]:
    """The next checkpoint this alert has reached and not yet recorded."""
    age = now - float(doc.get("created_at") or now)
    done = doc.get("checks") or {}
    for label, secs in CHECKPOINTS:
        if age >= secs and label not in done:
            return label, secs
    return None


async def _run_once(client) -> int:
    """One pass. Returns how many GMGN calls were made."""
    now = time.time()
    calls = 0
    try:
        docs = await _col().find({"done": False}).to_list(500)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[OUTCOME] could not read pending: {exc}")
        return 0

    for doc in docs:
        if calls >= MAX_PER_CYCLE:
            break

        # A chain with no price source is recorded and closed immediately —
        # keeping it open would clog the queue and imply a reading is coming.
        if not is_priceable(doc.get("chain")):
            await _col().update_one({"_id": doc["_id"]},
                                    {"$set": {"done": True, "price_source": "none"}})
            continue

        # Give up on anything past the last checkpoint, so the queue drains.
        if now - float(doc.get("created_at") or now) > STALE_AFTER:
            await _col().update_one({"_id": doc["_id"]}, {"$set": {"done": True}})
            continue

        # The entry price is taken on the first pass, not at alert time: the
        # alert path must not wait on a network call.
        if doc.get("entry_price") is None:
            price = await _price(client, doc.get("chain"), doc["address"], doc)
            calls += 1
            if price is None:
                continue
            await _col().update_one({"_id": doc["_id"]},
                                    {"$set": {"entry_price": price, "entry_at": now}})
            doc["entry_price"] = price
            continue

        due = _due(doc, now)
        if not due:
            continue
        label, _secs = due
        price = await _price(client, doc.get("chain"), doc["address"], doc)
        calls += 1
        if price is None:
            continue

        entry = float(doc["entry_price"])
        change = ((price - entry) / entry * 100) if entry else 0.0
        checks = dict(doc.get("checks") or {})
        checks[label] = {"price": price, "change_pct": round(change, 2), "at": now}
        finished = len(checks) >= len(CHECKPOINTS)
        await _col().update_one(
            {"_id": doc["_id"]},
            {"$set": {"checks": checks, "done": finished,
                      "best_pct": max([c["change_pct"] for c in checks.values()])}},
        )
        log.info(f"[OUTCOME] {doc.get('symbol')} {label}: {change:+.1f}% "
                 f"({doc.get('source')})")
        if label in REPLY_CHECKPOINTS:
            await _reply_with_result(doc, label, change, price)
    return calls


async def _reply_with_result(doc: dict, label: str, change: float,
                             price: float) -> None:
    """Post the result as a reply to the alert that started it.

    The point is that the answer arrives where the question was asked. A result
    on the dashboard is only seen by someone who goes looking; a reply under the
    original alert is seen by everyone who read it.
    """
    chat_id = doc.get("tg_chat_id")
    message_id = doc.get("tg_message_id")
    if not chat_id or not message_id:
        return                      # alert predates this, or never reached Telegram

    from .scanners import scfg
    if not scfg.TELEGRAM_BOT_TOKEN_SET:
        return
    mark = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
    text = (f"{mark} <b>{esc(doc.get('symbol') or '?')}</b> — {label}: "
            f"<b>{change:+.1f}%</b>\n"
            f"<i>entry ${float(doc.get('entry_price') or 0):.10f} "
            f"→ now ${price:.10f}</i>")

    import aiohttp
    url = f"https://api.telegram.org/bot{scfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_to_message_id": int(message_id),
        # The alert may have been deleted, or be too old to reply to. Sending it
        # unattached beats not sending it at all.
        "allow_sending_without_reply": True,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload,
                              timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status != 200:
                    log.debug(f"[OUTCOME] reply failed {r.status}: "
                              f"{(await r.text())[:120]}")
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[OUTCOME] reply send failed: {exc}")


async def watch() -> None:
    """Supervisor task. Uses the scanners' GMGN client, so it shares their
    rate limit rather than opening a second one."""
    from . import supervisor
    log.info(f"[OUTCOME] tracker started — checkpoints "
             f"{', '.join(l for l, _ in CHECKPOINTS)}, max {MAX_PER_CYCLE} lookups/cycle")
    while True:
        try:
            await asyncio.sleep(CYCLE_SECONDS)
            client = getattr(supervisor, "_client", None)
            if client is None:
                continue          # scanners not up — nothing to borrow
            await _run_once(client)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[OUTCOME] cycle failed: {exc}")


# ── Reporting ──────────────────────────────────────────────────────────────────

def _summarise(docs: list[dict]) -> dict:
    """Per-checkpoint averages plus a hit rate, from rows that have a reading.

    Rows on a chain with no price source are counted separately — folding them
    into `tracked` would quietly drag every average toward nothing.
    """
    unpriced = [d for d in docs if d.get("price_source") == "none"]
    docs = [d for d in docs if d.get("price_source") != "none"]
    out: dict[str, Any] = {"tracked": len(docs), "unpriceable": len(unpriced)}
    for label, _secs in CHECKPOINTS:
        vals = [c["change_pct"] for d in docs
                if (c := (d.get("checks") or {}).get(label))]
        if vals:
            out[label] = {
                "n": len(vals),
                "avg_pct": round(sum(vals) / len(vals), 2),
                "best_pct": round(max(vals), 2),
                "worst_pct": round(min(vals), 2),
                # "hit" = actually went up. The number that decides whether a
                # threshold is earning its place.
                "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100),
            }
        else:
            out[label] = None
    return out


async def summary(source: Optional[str] = None, days: int = 7) -> dict:
    since = time.time() - days * 86400
    flt: dict = {"created_at": {"$gte": since}}
    if source:
        flt["source"] = source
    docs = await _col().find(flt).to_list(5000)
    by_source: dict[str, list[dict]] = {}
    for d in docs:
        by_source.setdefault(d.get("source") or "?", []).append(d)
    return {
        "days": days,
        "overall": _summarise(docs),
        "by_source": {k: _summarise(v) for k, v in sorted(by_source.items())},
    }


async def recent(limit: int = 50, source: Optional[str] = None) -> list[dict]:
    flt = {"source": source} if source else {}
    docs = await _col().find(flt).to_list(2000)
    docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    for d in docs:
        d.pop("_id", None)
    return docs[:limit]


async def by_group(days: int = 30, min_calls: int = 1) -> list[dict]:
    """Premium groups ranked by how their calls actually did.

    You have 112 groups and per-day message counts, but nothing said which
    ones produce winners — so the enable/disable switch had no evidence behind
    it. A group is credited for every token it called, using the best reading
    recorded for that token.
    """
    since = time.time() - days * 86400
    docs = await _col().find(
        {"source": SRC_PREMIUM, "created_at": {"$gte": since}}
    ).to_list(5000)

    agg: dict[str, dict] = {}
    for d in docs:
        checks = d.get("checks") or {}
        if not checks:
            continue                      # no reading yet — cannot judge it
        best = max(c["change_pct"] for c in checks.values())
        one_h = (checks.get("1h") or {}).get("change_pct")
        for g in (d.get("groups") or []):
            a = agg.setdefault(g, {"group": g, "calls": 0, "wins": 0,
                                   "best_sum": 0.0, "h1": [], "top": None})
            a["calls"] += 1
            a["best_sum"] += best
            if best > 0:
                a["wins"] += 1
            if one_h is not None:
                a["h1"].append(one_h)
            if a["top"] is None or best > a["top"]["pct"]:
                a["top"] = {"symbol": d.get("symbol"), "pct": round(best, 1)}

    rows = []
    for a in agg.values():
        if a["calls"] < min_calls:
            continue
        rows.append({
            "group": a["group"],
            "calls": a["calls"],
            "hit_rate": round(a["wins"] / a["calls"] * 100),
            "avg_best_pct": round(a["best_sum"] / a["calls"], 1),
            "avg_1h_pct": round(sum(a["h1"]) / len(a["h1"]), 1) if a["h1"] else None,
            "top_call": a["top"],
        })
    # Hit rate first, then size of the average move — a group that is right
    # often but only just is less useful than one that is right often and big.
    rows.sort(key=lambda r: (r["hit_rate"], r["avg_best_pct"]), reverse=True)
    return rows
