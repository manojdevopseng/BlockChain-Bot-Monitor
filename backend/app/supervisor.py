"""Supervisor — owns the lifecycle of the real background scanners.

It reconciles desired state (from the registry toggles) against running asyncio
tasks. A toggle flip calls `handle_toggle` → `reconcile`, which starts/stops the
SOL / ETH / Robinhood workers live (no restart).

The SOL scanner is shared: it runs whenever SOL→ETH *or* SOL→Robinhood is on,
because both read `sol_scanner.active_watched_tickers`.

Scanner dependencies (curl_cffi / aiohttp / websockets) are imported lazily. If
they aren't installed (e.g. a bare dev box that only did the Phase-1 install),
the app still runs fine in dashboard-only mode — it just logs a warning.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from . import registry

_started_at: float = 0.0
_available: bool = False
_import_error: str = ""

# Lazily-created shared objects (only when a scanner is first needed).
_client = None            # GMGNClient
_sol = None               # SolanaScanner
_watchdog: Optional[asyncio.Task] = None   # heartbeat.watch()
_outcomes: Optional[asyncio.Task] = None   # outcomes.watch()
_ai_agent: Optional[asyncio.Task] = None  # ai_agent.watch()
_x_feed: Optional[asyncio.Task] = None    # ai_agent.x_feed_watch()
_digest: Optional[asyncio.Task] = None     # digest.watch()
_instances: dict = {}     # logical name -> scanner instance ('eth' | 'rbh')
_tasks: dict[str, asyncio.Task] = {}   # logical name -> task ('sol' | 'eth' | 'rbh')

# Which registry service governs each logical worker.
_SVC_ETH = "sol_to_eth"
_SVC_RBH = "sol_to_rbh"


def _try_import() -> bool:
    global _available, _import_error
    try:
        import aiohttp  # noqa: F401
        import websockets  # noqa: F401
        from curl_cffi.requests import AsyncSession  # noqa: F401
        _available = True
    except Exception as exc:  # noqa: BLE001
        _available = False
        _import_error = str(exc)
    return _available


async def start() -> None:
    global _started_at
    _started_at = time.time()
    registry.on_change(handle_toggle)

    # Preload persisted scanner state from Mongo into the storage cache.
    from .scanners import storage_repo
    await storage_repo.preload()

    if not _try_import():
        print(f"[supervisor] scanner deps not installed ({_import_error}); "
              f"dashboard-only mode. `pip install -r requirements.txt` to enable scanners.")
        return
    await reconcile()

    # Watches for a component that is "running" but has stopped doing anything.
    global _watchdog
    from . import heartbeat
    await heartbeat.load()      # so a restart does not blank "last activity"
    _watchdog = asyncio.create_task(heartbeat.watch(), name="watchdog")

    # The outcome tracker is started by reconcile() instead, so its Settings
    # switch can stop and start it live like any other worker.
    global _digest
    from . import digest
    _digest = asyncio.create_task(digest.watch(), name="digest")


async def stop() -> None:
    # No `global` declaration: the loop clears these through globals()[attr],
    # so declaring them global here did nothing at all.
    for attr in ("_watchdog", "_outcomes", "_digest", "_ai_agent", "_x_feed"):
        task = globals().get(attr)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        globals()[attr] = None
    for name in list(_tasks):
        await _stop_worker(name)
    global _client
    if _client is not None:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None
    try:
        # Counts buffered since the last flush would otherwise be lost on stop.
        from . import fwd_counters, heartbeat
        await fwd_counters.flush()
        await heartbeat.save()
    except Exception:
        pass
    try:
        from .scanners.gas_tracker import gas_tracker
        await gas_tracker.close()
    except Exception:
        pass


async def handle_toggle(service_id: str, enabled: bool) -> None:
    # Any toggle affecting a scanner just triggers a full reconcile.
    if _available:
        await reconcile()


async def reconcile() -> None:
    """Compute desired workers from the registry and start/stop to match."""
    if not _available:
        return
    enabled = await registry.enabled_map()

    from .scanners import scfg
    scfg.refresh_from_registry(enabled)

    want_eth = bool(enabled.get(_SVC_ETH)) and bool(enabled.get("chain_eth", True))
    want_rbh = bool(enabled.get(_SVC_RBH)) and bool(enabled.get("chain_rbh", True))
    want_sol = want_eth or want_rbh
    want_fwd = bool(enabled.get("forwarder")) and scfg.TELETHON_ENABLED
    # Commands ride the bot token, not the userbot session — so they can run
    # even while the forwarder is logged out.
    want_cmd = bool(enabled.get("bot_commands")) and scfg.TELEGRAM_BOT_TOKEN_SET

    want: set[str] = set()
    if want_sol:
        want.add("sol")
    if want_eth:
        want.add("eth")
    if want_rbh:
        want.add("rbh")
    # Its own worker, its own endpoints: it runs whether or not the Robinhood
    # cross-chain scanner does, and stopping one must not stop the other.
    #
    # One worker, two panels, either of which is reason enough to run it: the
    # X Monitor and the Launchpad Monitor share this socket, so switching the
    # X one off used to take the Launchpad one down with it. Each panel decides
    # for itself whether to write, in _handle.
    if ((bool(enabled.get("rbhx_monitor")) or bool(enabled.get("launchpad_monitor")))
            and bool(enabled.get("rbhx_rpc", True))):
        want.add("rbhx")
    # The RSI tracker runs on its own switch: it prices tokens the user added
    # by hand and has nothing to do with whether a scanner is on.
    if bool(enabled.get("rsi_tracker")):
        want.add("rsi")
    if want_fwd:
        want.add("fwd")
    if want_cmd:
        want.add("cmd")

    # Stop workers no longer wanted.
    for name in list(_tasks):
        if name not in want:
            await _stop_worker(name)

    # Start newly-wanted workers. A worker that fails to start (bad creds,
    # missing Telethon session, unreachable RPC) must never take the app or the
    # other workers down — log it and carry on. The error reaches Telegram via
    # the ERROR log handler.
    for name in ("sol", "eth", "rbh", "rbhx", "rsi", "fwd", "cmd"):
        if name in want and name not in _tasks:
            try:
                await _start_worker(name)
            except Exception as exc:  # noqa: BLE001
                _tasks.pop(name, None)
                _instances.pop(name, None)
                from .scanners.slog import get_logger
                get_logger("supervisor").error(f"[{name}] worker failed to start: {exc}")

    # The outcome tracker is a standalone task rather than a scanner worker,
    # but its switch has to behave the same: turning it off stops the task, not
    # just its output.
    from . import ai_agent, outcomes
    await _set_standalone("_outcomes", bool(enabled.get("outcome_tracker", True)),
                          outcomes.watch, "outcomes")
    await _set_standalone("_ai_agent", bool(enabled.get("ai_agent")),
                          ai_agent.watch, "ai-agent")
    # Its own switch rather than the chain's or the model's: the feed is useful
    # with Grok off, and sometimes wants stopping without touching SOL.
    await _set_standalone("_x_feed", bool(enabled.get("x_feed", True)),
                          ai_agent.x_feed_watch, "x-feed")

    # Push the live toggle map into a running userbot so its per-source gates
    # (CallAnalyser2 / BuyBotTracker / DexSignalCall / ETH Otto) and premium
    # features update live, without restarting the Telethon session.
    fwd = _instances.get("fwd")
    if fwd is not None:
        fwd.set_enabled_map(enabled)

    # Same for the Robinhood monitor, which now serves two panels off one
    # worker: switching one panel off leaves the worker running for the other,
    # so the switch has to reach it. It re-reads them itself once a minute
    # anyway — this is what makes the change land on the next launch instead.
    rbhx = _instances.get("rbhx")
    if rbhx is not None:
        rbhx.apply_toggles(enabled)

    # Same for the RSI tracker: a chain switched off must stop being sampled on
    # the next tick, not at its next minute-long refresh.
    rsi = _instances.get("rsi")
    if rsi is not None:
        rsi.apply_toggles(enabled)


async def _set_standalone(attr: str, want: bool, factory, name: str) -> None:
    """Start or stop one of the standalone background tasks to match a toggle.

    These are not scanner workers — nothing depends on them and they hold no
    connections — so they are tracked as module globals rather than in _tasks.
    """
    task = globals().get(attr)
    alive = task is not None and not task.done()
    if want and not alive:
        globals()[attr] = asyncio.create_task(factory(), name=name)
    elif not want and alive:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        globals()[attr] = None


async def _ensure_core() -> None:
    """Create + start the shared GMGN client and SOL scanner on demand."""
    global _client, _sol
    if _client is None:
        from .scanners.gmgn_client import GMGNClient
        from .scanners import scfg
        # Deliberately slower than API_RATE_LIMIT — gmgn.ai's Cloudflare 403s a
        # datacenter IP that polls too fast. Tunable via GMGN_SCAN_RATE.
        _client = GMGNClient(rate_limit=scfg.GMGN_SCAN_RATE)
        await _client.start()
    if _sol is None:
        from .scanners.sol_scanner import SolanaScanner
        _sol = SolanaScanner(_client)


async def _start_worker(name: str) -> None:
    import aiohttp
    await _ensure_core()

    if name == "sol":
        _tasks["sol"] = asyncio.create_task(_sol.run(), name="sol-scanner")
        return
    if name == "eth":
        from .scanners.eth_scanner import EthTrendingScanner
        inst = EthTrendingScanner(sol_scanner=_sol, session_factory=aiohttp.ClientSession)
        _instances["eth"] = inst
        _tasks["eth"] = asyncio.create_task(inst.run(), name="eth-scanner")
        return
    if name == "rsi":
        from .scanners.rsi_tracker import RsiTracker
        inst = RsiTracker()
        inst.apply_toggles(await registry.enabled_map())
        _instances["rsi"] = inst
        _tasks["rsi"] = asyncio.create_task(inst.run(), name="rsi-tracker")
        return

    if name == "rbhx":
        from .scanners.rbhx_monitor import RbhXMonitor
        inst = RbhXMonitor()
        # Toggles before start: which pair versions it subscribes to is fixed
        # when the detector is built.
        inst.apply_toggles(await registry.enabled_map())
        _instances["rbhx"] = inst
        _tasks["rbhx"] = asyncio.create_task(inst.run(), name="rbhx-monitor")
        return

    if name == "rbh":
        from .scanners.robinhood_scanner import RobinhoodScanner
        inst = RobinhoodScanner(sol_scanner=_sol, session_factory=aiohttp.ClientSession)
        _instances["rbh"] = inst
        _tasks["rbh"] = asyncio.create_task(inst.run(), name="rbh-scanner")
        return
    if name == "cmd":
        from .scanners.commands import TelegramCommands
        inst = TelegramCommands()
        await inst.start()          # registers the "/" menu
        _instances["cmd"] = inst
        _tasks["cmd"] = asyncio.create_task(inst.run(), name="tg-commands")
        return
    if name == "fwd":
        from .scanners.userbot import TelegramForwarder
        inst = TelegramForwarder()
        inst.set_enabled_map(await registry.enabled_map())
        await inst.start()   # connects Telethon + registers handlers
        _instances["fwd"] = inst
        _tasks["fwd"] = asyncio.create_task(inst.run(), name="tg-forwarder")


async def _stop_worker(name: str) -> None:
    task = _tasks.pop(name, None)
    inst = _instances.pop(name, None)
    # Forwarder needs a clean Telethon disconnect before its run() is cancelled.
    if name in ("fwd", "cmd") and inst is not None:
        try:
            await inst.stop()
        except Exception:
            pass
    if isinstance(task, asyncio.Task) and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    # If neither cross-chain scanner is running any more, drop the SOL scanner too.
    if name in ("eth", "rbh") and "eth" not in _tasks and "rbh" not in _tasks and "sol" in _tasks:
        await _stop_worker("sol")


async def restart_worker(name: str) -> bool:
    """Stop a worker and let reconcile bring it back.

    Some settings are only read when a scanner builds its chain spec (which
    Uniswap versions to subscribe to, for one), so changing them needs that
    worker recreated — not the whole process.
    """
    if not _available:
        return False
    await _stop_worker(name)
    await reconcile()
    return _worker_alive(name)


def instance(name: str):
    """The live worker object, so routers can talk to a running worker
    (e.g. re-publishing the Telegram command menu after a toggle)."""
    return _instances.get(name)


def kick_sol_discovery() -> None:
    """Wake SOL's on-chain discovery to retry with a newly saved endpoint now,
    instead of it sitting out whatever backoff it was already in (up to 60s).

    Not routed through `instance()`/`_instances` — SOL isn't tracked there
    (only eth/rbh/cmd/fwd are); `_sol` is the SolanaScanner global reconcile()
    creates. A no-op before it exists or once it's been torn down.
    """
    if _sol is not None:
        _sol.kick_discovery()


def uptime_seconds() -> int:
    return int(time.time() - _started_at) if _started_at else 0


def _worker_alive(name: str) -> bool:
    t = _tasks.get(name)
    return isinstance(t, asyncio.Task) and not t.done()


def rpc_connected(name: str) -> bool:
    """Is this chain's RPC socket actually connected right now?

    Deliberately not the same question as `_worker_alive`. ETH/Robinhood's
    WSProvider and SOL's on-chain discovery both retry forever on a rejection
    (a 429 loop never lets the task finish), so "the task hasn't crashed" is
    true the entire time a chain is fully rate-limited and seeing nothing.
    Reads the real per-socket signal where one exists.
    """
    if name in ("eth", "rbh", "rbhx"):
        inst = _instances.get(name)
        return bool(inst is not None and getattr(inst, "connected", False))
    if name == "sol":
        # Distinct from chain_sol/rpc_sol's task-alive status elsewhere: the
        # GMGN rolling feed can be working fine while Helius discovery is
        # 429-looping, so this answers specifically "is the SOL WSS up".
        return bool(_sol is not None and _sol.discovery_connected())
    return _worker_alive(name)


def rpc_active_url(name: str) -> str:
    """Which endpoint this chain's socket is dialling right now.

    A pool of two or three rotates on rejection, so the configured list does
    not say which one is in use — RPC Monitor needs this to tell the live slot
    apart from the standby ones. "" when there is nothing running to ask.
    """
    if name in ("eth", "rbh", "rbhx"):
        inst = _instances.get(name)
        return getattr(inst, "active_endpoint", "") if inst is not None else ""
    if name == "sol":
        return _sol.discovery_endpoint() if _sol is not None else ""
    return ""


def status() -> dict[str, str]:
    """service_id -> 'running' | 'stopped' for the workers we own directly."""
    return {
        _SVC_ETH: "running" if _worker_alive("eth") else "stopped",
        _SVC_RBH: "running" if _worker_alive("rbh") else "stopped",
        "rbhx_monitor": "running" if _worker_alive("rbhx") else "stopped",
        "rsi_tracker": "running" if _worker_alive("rsi") else "stopped",
        "forwarder": "running" if _worker_alive("fwd") else "stopped",
        "bot_commands": "running" if _worker_alive("cmd") else "stopped",
    }


# Every registry service maps to the worker that actually does its work. A
# toggle being on says nothing about whether that worker is alive — every
# userbot feature is dead if the Telethon session never logged in, and the gas
# monitor rides the ETH socket.
_DEPENDS_ON = {
    # Userbot source channels (ids match the channel names in .env).
    "callanalyser2":          "fwd",
    "buybottracker":          "fwd",
    "dexsignalcall":          "fwd",
    "eth_otto_group":         "fwd",
    # Userbot premium features.
    "premium_callers_signal": "fwd",
    "premium_eth_detection":    "fwd",
    "premium_rbh_detection":    "fwd",
    "premium_sol_detection":    "fwd",
    "forwarder":              "fwd",
    # Cross-chain flows and gas, on their chain's socket.
    "sol_to_eth":             "eth",
    "sol_to_rbh":             "rbh",
    "eth_gas_fees":           "eth",
    "chain_eth":              "eth",
    "chain_rbh":              "rbh",
    "chain_sol":              "sol",
    "rpc_eth":                "eth",
    "rpc_rbh":                "rbh",
    "rpc_sol":                "sol",
    # Every switch the monitor reads at start rather than per message.
    "rsi_tracker":            "rsi",
    "rsi_telegram":           "rsi",
    "rsi_chain_rbh":          "rsi",
    "rsi_chain_eth":          "rsi",
    "rsi_chain_bsc":          "rsi",
    "rsi_chain_sol":          "rsi",
    "rsi_rpc_rbh":            "rsi",
    "rsi_rpc_eth":            "rsi",
    "rsi_rpc_bsc":            "rsi",
    "rsi_rpc_sol":            "rsi",
    "rbhx_monitor":           "rbhx",
    # Here because it can now be the only reason the worker is running: with
    # the X Monitor off, switching this on has to start it rather than wait for
    # the next restart.
    "launchpad_monitor":      "rbhx",
    "rbhx_rpc":               "rbhx",
    "rbhx_v2v3":              "rbhx",
    "bot_commands":           "cmd",
}

# Which dependencies have a real per-socket "connected" signal worth
# preferring over plain task-alive. ETH/Robinhood's WSProvider tracks it
# already; SOL's task-alive is left as-is here on purpose (see rpc_connected) —
# the GMGN feed a "sol" dependent actually cares about keeps working through a
# Helius outage, so switching this one to socket-state would trade one
# misleading status for another.
_CONNECTED_AWARE = {"eth", "rbh"}

_WHY_DOWN = {
    "fwd": "Telegram userbot not logged in (no .session)",
    "eth": "ETH RPC not reachable — down, rate-limited, or not configured",
    "rbh": "Robinhood RPC not reachable — down, rate-limited, or not configured",
    "sol": "Solana scanner not running",
    "cmd": "TELEGRAM_BOT_TOKEN not set (get one from @BotFather)",
}


def service_states(enabled: dict[str, bool]) -> dict[str, dict]:
    """service_id -> {status, reason, depends_on} using real worker state.

    Single source of truth so the dashboard and the system page can never
    disagree about whether something is actually up. For eth/rbh dependents
    this means the actual socket state (rpc_connected), not just whether the
    reconnect-loop task is still alive — that task never dies even while a
    quota rejection has it retrying forever, so "alive" alone said "running"
    for a chain that had been fully down for the better part of an hour.
    """
    workers = diagnostics().get("workers", {})
    out: dict[str, dict] = {}
    for sid, on in enabled.items():
        dep = _DEPENDS_ON.get(sid)
        if not on:
            status_, reason = "disabled", "turned off in Settings"
        elif dep is None:
            status_, reason = "running", ""
        elif dep in _CONNECTED_AWARE:
            status_, reason = (("running", "") if rpc_connected(dep)
                              else ("stopped", _WHY_DOWN.get(dep, "worker not running")))
        elif workers.get(dep):
            status_, reason = "running", ""
        else:
            status_, reason = "stopped", _WHY_DOWN.get(dep, "worker not running")
        out[sid] = {"status": status_, "reason": reason, "depends_on": dep}
    return out


def diagnostics() -> dict:
    return {
        "scanner_deps_available": _available,
        "import_error": _import_error,
        "workers": {n: _worker_alive(n) for n in ("sol", "eth", "rbh", "fwd", "cmd")},
    }
