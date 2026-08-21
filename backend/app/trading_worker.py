"""The trading engine's own clock.

Auto-sell and the daily loss limit are the two rules nobody can be sitting at
the screen for — a take-profit that only fires while the page is open is not a
take-profit. So they run here, once a minute, for every account that has
something open.

One pass prices every open position across every account in a single batched
read (see trading.prices), so the cost of this is one or two HTTP requests a
minute however many accounts are on the box.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp

from . import db, trading
from .scanners.slog import get_logger

log = get_logger(__name__)

# A minute. Fast enough that a stop-loss is a stop-loss, slow enough that
# DexScreener never notices us.
INTERVAL = 60


async def _accounts() -> list[str]:
    """Every account worth a pass: one holding something, or armed to.

    `distinct` is deliberately not used — the in-memory backend the previews
    run on does not implement it, and this is small either way.
    """
    users: set[str] = set()
    rows = await db.get_collection("trading_positions").find(
        {"status": "open"}, {"user": 1}).to_list(5000)
    for r in rows:
        if r.get("user"):
            users.add(r["user"])
    return sorted(users)



# How often the protected relays are checked. Rarely, because this is not a
# thing that changes minute to minute — and because the check itself costs a
# request to each of them.
RELAY_EVERY = 15 * 60
_relay_checked = 0.0
# Which chains were already reported down, so a relay that stays down for a
# day is one notice rather than ninety-six.
_relay_down: set[str] = set()


async def _check_relays() -> None:
    """Tell the operator when a protected route stops answering.

    This is the quietest failure in the whole trading path. Nothing breaks:
    the switch stays on, the panel keeps saying "protected", and every order
    goes out the ordinary way — the relay is simply not there any more. It
    can only be found by asking, so it is asked.
    """
    global _relay_checked
    now = time.time()
    if now - _relay_checked < RELAY_EVERY:
        return
    _relay_checked = now

    from . import mev, notifications
    for row in await mev.status():
        if not row["supported"]:
            continue
        chain = row["chain"]
        if row["reachable"]:
            if chain in _relay_down:
                _relay_down.discard(chain)
                log.info(f"[TRADING] {chain.upper()} relay is answering again")
            continue
        if chain in _relay_down:
            continue
        _relay_down.add(chain)
        log.warning(f"[TRADING] {chain.upper()} MEV relay unreachable: "
                    f"{row['why']}")
        try:
            await notifications.notify(
                "admin", notifications.SYSTEM,
                f"{chain.upper()} MEV relay is not answering",
                f"{row['relay'] or 'The relay'} stopped responding"
                + (f" ({row['why']})" if row["why"] else "")
                + f". Orders on {chain.upper()} would go out unprotected until "
                  f"it returns.",
                "/trading", key=f"relay-down-{chain}-{int(now // 3600)}")
        except Exception:  # noqa: BLE001
            pass


async def _tick(session: aiohttp.ClientSession) -> None:
    await _check_relays()

    # Queued gas-fee buys first: a token that has just become sellable should
    # be opened before this pass prices anything, not a minute after.
    try:
        gas = await trading.sweep_pending(session)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[TRADING] gas queue failed: {type(exc).__name__}: {exc}")
    else:
        for b in gas["bought"]:
            log.info(f"[TRADING] gas auto-buy {b['symbol']} ({b['chain']}) "
                     f"at {b['entry']}")
        for d in gas["dropped"]:
            log.info(f"[TRADING] gas auto-buy skipped {d['symbol']}: {d['why']}")

    for user in await _accounts():
        try:
            out = await trading.run_rules(user, session)
        except Exception as exc:  # noqa: BLE001
            # One account's bad row must not stop the pass for the others.
            log.warning(f"[TRADING] rules failed for {user}: "
                        f"{type(exc).__name__}: {exc}")
            continue
        for s in out["sold"]:
            log.info(f"[TRADING] auto-sold {s['symbol']} ({s['chain']}) "
                     f"for {user}: {s['why']}")
        if out["stopped"]:
            log.warning(f"[TRADING] auto-buy stopped for {user}: {out['stopped']}")


async def run() -> None:
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await _tick(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[TRADING] pass failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(INTERVAL)
