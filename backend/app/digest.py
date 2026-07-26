"""One message a day: what fired, and whether it was any good.

Per-event alerts tell you what is happening now. They do not tell you whether
yesterday was a good day, which source is carrying its weight, or which group
is producing winners. This sends that once a day to the alert group.

Sent at DIGEST_HOUR IST. The send is tracked by IST date, so a restart cannot
send it twice and a process that was down over the hour still sends it when it
comes back — a digest an hour late is worth more than none.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from . import db, outcomes
from .scanners import scfg
from .scanners.slog import get_logger
from .util import IST, esc, ist_date_str

log = get_logger(__name__)

CHECK_SECONDS = 300          # a five-minute granularity is plenty for a daily job
_STATE = "digest_state"


async def _last_sent_day() -> str:
    doc = await db.get_collection("scanner_state").find_one({"name": _STATE})
    return str((doc or {}).get("data") or "")


async def _mark_sent(day: str) -> None:
    await db.get_collection("scanner_state").update_one(
        {"name": _STATE},
        {"$set": {"name": _STATE, "kind": "str", "data": day, "saved_at": time.time()}},
        upsert=True,
    )


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"


async def build(days: int = 1) -> str:
    """The digest text. Separate from sending so it can be previewed."""
    since = time.time() - days * 86400
    alerts = db.get_collection("alerts")
    xchain = await alerts.count_documents(
        {"type": "Cross-Chain Match", "created_at": {"$gte": since}})
    gas = await db.get_collection("gas_alerts").count_documents(
        {"created_at": {"$gte": since}})
    dets = await db.get_collection("premium_detections").count_documents(
        {"ts": {"$gte": since}})

    lines = [
        f"📅 <b>Daily digest — {ist_date_str(time.time())}</b>",
        "",
        f"<b>Fired in the last {days * 24}h</b>",
        f"• Cross-chain matches: {xchain}",
        f"• High-gas early buys: {gas}",
        f"• Premium detections: {dets}",
    ]

    perf = await outcomes.summary(days=max(days, 7))
    by_src = perf.get("by_source") or {}
    if by_src:
        lines += ["", "<b>How the alerts did (7d, at 1h)</b>"]
        pretty = {"xchain_eth": "SOL→ETH", "xchain_rbh": "SOL→RBH",
                  "gas": "High-gas", "premium": "Premium calls"}
        for src, stats in by_src.items():
            h1 = stats.get("1h")
            if not h1:
                continue
            lines.append(
                f"• {pretty.get(src, src)}: {h1['n']} tracked, "
                f"avg {_pct(h1['avg_pct'])}, {h1['hit_rate']}% up, "
                f"best {_pct(h1['best_pct'])}"
            )
        if len(lines) and lines[-1].startswith("<b>How"):
            lines.append("• nothing has reached the 1h checkpoint yet")

    groups = await outcomes.by_group(days=30, min_calls=2)
    if groups:
        lines += ["", "<b>Top groups (30d, 2+ calls)</b>"]
        for g in groups[:5]:
            top = g.get("top_call") or {}
            lines.append(
                f"• {esc(g['group'])}: {g['calls']} calls, {g['hit_rate']}% up, "
                f"avg best {_pct(g['avg_best_pct'])}"
                + (f" · top {esc(top.get('symbol'))} {_pct(top.get('pct'))}" if top else "")
            )

    if xchain == 0 and gas == 0 and dets == 0:
        lines += ["", "<i>Nothing fired. Scanners were up — the market was quiet.</i>"]
    return "\n".join(lines)


async def _send() -> bool:
    from . import notifier
    text = await build(days=1)
    return await notifier.send(text, silent=True)


async def watch() -> None:
    """Supervisor task. Sends once per IST day at DIGEST_HOUR."""
    if not scfg.DIGEST_ENABLED:
        log.info("[DIGEST] disabled (DIGEST_ENABLED=false)")
        return
    log.info(f"[DIGEST] started — daily at {scfg.DIGEST_HOUR:02d}:00 IST")
    while True:
        try:
            await asyncio.sleep(CHECK_SECONDS)
            now = datetime.now(IST)
            today = ist_date_str(time.time())
            if now.hour < scfg.DIGEST_HOUR:
                continue
            if await _last_sent_day() == today:
                continue
            if await _send():
                await _mark_sent(today)
                log.info(f"[DIGEST] sent for {today}")
            else:
                # Mark it anyway: a broken send retried every 5 minutes for the
                # rest of the day is worse than a missing digest.
                await _mark_sent(today)
                log.warning(f"[DIGEST] send failed for {today} — not retrying today")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[DIGEST] cycle failed: {exc}")
