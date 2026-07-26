"""Liveness per component — "when did this last actually do something?"

A scanner can be `running` and producing nothing: the task is alive, the socket
says connected, and the dashboard shows green while no work happens. This
records the last time each part of the bot did its job, so silence is visible.

Two kinds of component, and the difference matters:

  • TICK  — something with a regular beat (the SOL scan loop runs every few
            seconds, a WebSocket is either connected or it is not). Silence
            here is a fault, so it is alerted.
  • EVENT — something that fires when the market does (a new pair, a
            cross-chain match, a premium message). Silence is normal at 4am
            and alerting on it would only teach you to ignore alerts. These
            are reported on the dashboard and never alerted.

HEALTH_ALERT_ENABLED / HEALTH_DOWN_SECONDS have been in the config since the
first commit with nothing reading them. This is the watchdog they were for.
"""

from __future__ import annotations

import asyncio
import time

from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)

TICK = "tick"
EVENT = "event"

# name -> (label, kind, quiet_after_seconds | None)
# quiet_after is only meaningful for TICK components.
COMPONENTS: dict[str, tuple[str, str, float | None]] = {
    "sol_scan":     ("SOL scanner cycle",      TICK,  300),
    "eth_ws":       ("ETH WebSocket",          TICK,  None),   # from down_seconds()
    "rbh_ws":       ("Robinhood WebSocket",    TICK,  None),
    "eth_pair":     ("New ETH pair",           EVENT, None),
    "rbh_pair":     ("New Robinhood pair",     EVENT, None),
    "premium_msg":  ("Premium group message",  EVENT, None),
    "xchain_match": ("Cross-chain match",      EVENT, None),
    "gas_alert":    ("High-gas early buy",     EVENT, None),
    "command":      ("Telegram command",       EVENT, None),
}

CHECK_SECONDS = 60

_last: dict[str, float] = {}
_quiet: set[str] = set()          # components currently reported as quiet
_task: asyncio.Task | None = None


def beat(name: str) -> None:
    """Record that `name` just did something. Sits in hot paths — a dict write
    and nothing else, no awaits and no database."""
    _last[name] = time.time()


def last_seen(name: str) -> float | None:
    return _last.get(name)


def _ws_down_seconds() -> dict[str, float | None]:
    """How long each chain's socket has been down, from the live detectors.

    None means that scanner is not running, which is not the watchdog's
    business — the supervisor already reports a stopped worker.
    """
    from . import supervisor
    out: dict[str, float | None] = {"eth_ws": None, "rbh_ws": None}
    for worker, key in (("eth", "eth_ws"), ("rbh", "rbh_ws")):
        inst = supervisor.instance(worker)
        detector = getattr(inst, "_detector", None) if inst is not None else None
        if detector is None:
            continue
        try:
            out[key] = float(detector.down_seconds())
        except Exception:
            out[key] = None
    return out


def snapshot() -> list[dict]:
    """Every component with how long since it last did something."""
    now = time.time()
    down = _ws_down_seconds()
    rows = []
    for name, (label, kind, quiet_after) in COMPONENTS.items():
        ts = _last.get(name)
        if name in down and down[name] is not None:
            # A connected socket is "doing something" continuously.
            connected = down[name] == 0
            rows.append({
                "name": name, "label": label, "kind": kind,
                "last_seen": now if connected else None,
                "age_seconds": 0 if connected else round(down[name]),
                "status": "ok" if connected else "quiet",
                "detail": "connected" if connected
                          else f"socket down {round(down[name])}s",
            })
            continue
        age = round(now - ts) if ts else None
        status = "unknown" if ts is None else (
            "quiet" if quiet_after and age and age > quiet_after else "ok"
        )
        rows.append({
            "name": name, "label": label, "kind": kind,
            "last_seen": ts, "age_seconds": age, "status": status,
            "detail": "" if ts else "nothing yet since start",
        })
    return rows


async def _check() -> None:
    from . import notifier

    down = _ws_down_seconds()
    now = time.time()

    for name, (label, kind, quiet_after) in COMPONENTS.items():
        if kind != TICK:
            continue

        if name in down:
            secs = down[name]
            if secs is None:
                _quiet.discard(name)          # scanner not running — not ours
                continue
            limit = scfg.HEALTH_DOWN_SECONDS
            is_quiet = secs > limit
            detail = f"WebSocket has been down for {round(secs / 60)} min"
        else:
            ts = _last.get(name)
            if ts is None:
                continue                       # never started — nothing to compare
            secs = now - ts
            limit = quiet_after or 0
            is_quiet = bool(limit and secs > limit)
            detail = f"no activity for {round(secs / 60)} min"

        if is_quiet and name not in _quiet:
            _quiet.add(name)
            log.error(f"[WATCHDOG] {label} — {detail}")
            await notifier.notify_error("Watchdog", f"{label}: {detail}")
        elif not is_quiet and name in _quiet:
            _quiet.discard(name)
            # Recovery is worth a line: otherwise the last thing you saw about
            # this component is that it broke.
            log.info(f"[WATCHDOG] {label} — back to normal")


async def watch() -> None:
    """Watchdog loop. Started by the supervisor, stopped with it."""
    if not scfg.HEALTH_ALERT_ENABLED:
        log.info("[WATCHDOG] disabled (HEALTH_ALERT_ENABLED=false)")
        return
    log.info(f"[WATCHDOG] started — checking every {CHECK_SECONDS}s, "
             f"WebSocket quiet after {scfg.HEALTH_DOWN_SECONDS}s")
    while True:
        try:
            await asyncio.sleep(CHECK_SECONDS)
            await _check()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[WATCHDOG] check failed: {exc}")
