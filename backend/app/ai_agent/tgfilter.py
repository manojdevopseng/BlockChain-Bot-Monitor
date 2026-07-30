"""The Telegram rule: which launches are worth a message.

A link that carried a burst of launches in a short window is a coordinated
push; one of those that also crosses the market cap bar inside its first
minute is what actually gets sent.
"""

from __future__ import annotations

import time
from typing import Optional

import aiohttp

from .. import pump_mcap
from ..config import settings
from ..util import ist_date_str
from .common import _col
from .notify import _notify_telegram


# ── Telegram: what a launch was actually worth ───────────────────────────────

async def _check_telegram(session: Optional[aiohttp.ClientSession],
                          address: str) -> bool:
    """Promote a launch to Telegram if it is in a link's burst AND cleared the bar.

    Two conditions that become true in either order and minutes apart: a token
    can cross $8k eight seconds in, while the fifth launch on its link does not
    exist until three minutes later. So this is called from every side that can
    change either answer — the decision being written, the bar being crossed,
    and the burst completing — and each time it asks the same question.

    Note this is the Telegram rule only. The model's gate is separate and does
    not care how many launches a link carried; it asks about the first and
    skips the copies.
    """
    dec = await _col("ai_decisions").find_one(
        {"address": address},
        {"address": 1, "telegram": 1, "symbol": 1, "name": 1,
         "narrative": 1, "verdict": 1, "peak_mcap_usd": 1, "link": 1})
    if not dec or dec.get("telegram"):
        return False                       # unknown, or already sent

    row = await _col("x_links").find_one(
        {"address": address},
        {"peak_mcap_usd": 1, "link": 1, "day": 1, "open_timestamp": 1}) or {}
    peak = max(float(dec.get("peak_mcap_usd") or 0),
               float(row.get("peak_mcap_usd") or 0),
               pump_mcap.peak_usd(address))
    # The cheap half first: only about one launch in twenty gets past this, so
    # the burst query below runs on those rather than on all of them.
    if peak < pump_mcap.threshold_usd():
        return False

    members = await _link_burst(row.get("link") or dec.get("link") or "",
                                row.get("day"), row.get("open_timestamp"))
    if address not in members:
        return False

    await _col("ai_decisions").update_one(
        {"address": address},
        {"$set": {"telegram": True, "peak_mcap_usd": round(peak),
                  "burst_position": members.index(address) + 1,
                  "telegram_at": time.time()}})
    await _notify_telegram(session, dec, peak, members.index(address) + 1)
    return True


async def _link_burst(link: str, day: Optional[str],
                      ts: Optional[float]) -> list[str]:
    """The first five launches on one link, if they arrived inside the window.

    Empty when the link has not carried five yet, or when the five took longer
    than the window. Names and tickers are not looked at at all — the same link
    under five different names is exactly the case this is for.

    Read from the collection rather than from memory so a restart mid-window
    cannot turn a real burst into one that never happened.
    """
    link = (link or "").strip()
    if not link:
        return []
    need = int(settings.ai_link_burst_count)
    window = int(settings.ai_link_burst_window)
    day = day or ist_date_str(ts or time.time())

    members = await _col("x_links").find(
        {"link": link, "day": day},
        {"address": 1, "open_timestamp": 1}
    ).sort("open_timestamp", 1).limit(need).to_list(need)
    if len(members) < need:
        return []
    span = (float(members[-1].get("open_timestamp") or 0)
            - float(members[0].get("open_timestamp") or 0))
    if span > window:
        return []
    return [m["address"] for m in members]


async def _burst_formed(link: str, day: str) -> None:
    """A launch was just written. If its link has now carried five inside the
    window, every one of those five is re-checked — the earlier ones may have
    crossed the bar minutes ago with nothing to promote them at the time.
    """
    members = await _link_burst(link, day, None)
    if not members:
        return
    for address in members:
        await _check_telegram(None, address)
async def _on_mcap_cross(mint: str, usd: float) -> None:
    """A watched launch just crossed the bar. Written down now, and sent if the
    launch has already been looked at — otherwise `_record` picks it up when it
    is.
    """
    await _col("x_links").update_one(
        {"address": mint}, {"$set": {"peak_mcap_usd": round(usd),
                                     "crossed_mcap": True}})
    await _check_telegram(None, mint)


async def _settle_mcaps() -> None:
    """Write down what the finished minutes reached, whether or not they crossed."""
    for mint, peak_sol, peak_usd in pump_mcap.expired():
        if peak_sol <= 0:
            continue
        await _col("x_links").update_one(
            {"address": mint},
            {"$set": {"peak_mcap_sol": round(peak_sol, 2),
                      "peak_mcap_usd": round(peak_usd)}})
        await _col("ai_decisions").update_one(
            {"address": mint}, {"$set": {"peak_mcap_usd": round(peak_usd)}})
