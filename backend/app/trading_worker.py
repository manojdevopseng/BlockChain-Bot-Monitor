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


async def _tick(session: aiohttp.ClientSession) -> None:
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
