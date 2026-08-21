"""Watching four addresses for the figures we quoted.

One loop, one question per rail: has our balance of this asset gone up, and
does the rise match a figure some order was quoted? If it does, that order is
paid and the plan starts by itself — nobody sends a screenshot and nobody waits
for an operator to be awake.

Reading a balance rather than scanning logs is what makes this cheap enough to
run for ever: one call per rail per pass, on any provider, with no block-range
limit to fall foul of. The cost of that choice is that two payments landing
inside one pass arrive as one number — so a rise is matched against single
orders first and then against pairs, and anything still unexplained is recorded
for the operator instead of being guessed at.

The baseline is stored, not assumed. On the very first pass a rail's balance is
simply written down: a running total that already existed is not a payment.
"""

from __future__ import annotations

import asyncio
import time
from itertools import combinations
from typing import Optional

import aiohttp

from app import notifications, orders, payments
from app.scanners.slog import get_logger

log = get_logger(__name__)

# How often each rail is asked. Fast enough that a buyer sees "paid" while the
# page is still open, slow enough to be nothing on anybody's quota.
CHECK_SECONDS = 30
# What counts as the same figure. Stablecoin transfers arrive exact, but a
# chain that rounds at the eighth decimal should not cost somebody their plan.
TOLERANCE = 0.0005


def _col():
    from app import db
    return db.get_collection("payment_rails")


class PaymentWatcher:
    def __init__(self, session_factory=aiohttp.ClientSession) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._reader: Optional[payments.BalanceReader] = None
        # Zero rather than now, so the first sweep happens on the first pass
        # after a restart rather than a day later.
        self._warned_at = 0.0

    async def run(self) -> None:
        self._session = self._session_factory()
        self._reader = payments.BalanceReader(self._session)
        rails = payments.available()
        log.info(f"[PAY] Payment watcher started — {len(rails)} rail(s): "
                 f"{', '.join(a.label for a in rails) or 'none configured'}")
        try:
            while True:
                try:
                    await asyncio.sleep(CHECK_SECONDS)
                    await orders.expire_stale()
                    for asset in payments.available():
                        await self._check(asset)
                    # Once a day, from whichever pass crosses the hour: telling
                    # somebody their plan ends tomorrow needs no loop of its own.
                    if time.time() - self._warned_at > 86400:
                        self._warned_at = time.time()
                        await notifications.warn_expiring()
                except asyncio.CancelledError:
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"[PAY] watcher: {exc}")
        finally:
            if self._session:
                await self._session.close()

    async def _check(self, asset: payments.Asset) -> None:
        asset_id = next((k for k, v in payments.ASSETS.items() if v is asset), "")
        balance = await self._reader.balance(asset)
        if balance is None:
            return
        row = await _col().find_one({"asset_id": asset_id}) or {}
        before = row.get("balance")
        await _col().update_one({"asset_id": asset_id},
                                {"$set": {"asset_id": asset_id,
                                          "balance": balance,
                                          "checked_at": time.time()}},
                                upsert=True)
        if before is None:
            # First sight of this rail: whatever is in there was not paid to us
            # today, and treating it as a payment would settle a random order.
            log.info(f"[PAY] {asset.label} baseline {balance}")
            return
        rise = round(balance - float(before), 6)
        if rise <= 0:
            return
        log.info(f"[PAY] {asset.label} +{rise}")
        await self._match(asset_id, rise)

    async def _match(self, asset_id: str, rise: float) -> None:
        """Which order — or which two — that rise pays for."""
        waiting = [o for o in await orders.open_orders(asset_id)]
        if not waiting:
            await orders.note_unmatched(asset_id, rise)
            return

        for order in waiting:
            if abs(float(order["amount"]) - rise) <= TOLERANCE:
                await orders.settle(order, rise)
                return

        # Two payments inside one pass read as one number. Only pairs: past
        # that the guesses outnumber the certainties, and a wrong guess starts
        # somebody else's subscription.
        for a, b in combinations(waiting, 2):
            if abs(float(a["amount"]) + float(b["amount"]) - rise) <= TOLERANCE:
                log.info(f"[PAY] {rise} settles two orders at once")
                await orders.settle(a, float(a["amount"]))
                await orders.settle(b, float(b["amount"]))
                return

        await orders.note_unmatched(asset_id, rise)
