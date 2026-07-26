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


async def stop() -> None:
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
        from . import fwd_counters
        await fwd_counters.flush()
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
    for name in ("sol", "eth", "rbh", "fwd", "cmd"):
        if name in want and name not in _tasks:
            try:
                await _start_worker(name)
            except Exception as exc:  # noqa: BLE001
                _tasks.pop(name, None)
                _instances.pop(name, None)
                from .scanners.slog import get_logger
                get_logger("supervisor").error(f"[{name}] worker failed to start: {exc}")

    # Push the live toggle map into a running forwarder so per-source gates
    # (BBCAnalyser2 / DexSignalCall / ETH otto / Premium Callers) update live.
    fwd = _instances.get("fwd")
    if fwd is not None:
        fwd.set_enabled_map(enabled)


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
        from .scanners.forwarder import TelegramForwarder
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


def uptime_seconds() -> int:
    return int(time.time() - _started_at) if _started_at else 0


def _worker_alive(name: str) -> bool:
    t = _tasks.get(name)
    return isinstance(t, asyncio.Task) and not t.done()


def status() -> dict[str, str]:
    """service_id -> 'running' | 'stopped' for the workers we own directly."""
    return {
        _SVC_ETH: "running" if _worker_alive("eth") else "stopped",
        _SVC_RBH: "running" if _worker_alive("rbh") else "stopped",
        "forwarder": "running" if _worker_alive("fwd") else "stopped",
        "bot_commands": "running" if _worker_alive("cmd") else "stopped",
    }


# Every registry service maps to the worker that actually does its work. A
# toggle being on says nothing about whether that worker is alive — the
# forwarder's four sub-features are all dead if the userbot never logged in,
# and the gas monitor rides the ETH socket.
_DEPENDS_ON = {
    "sol_to_eth":             "eth",
    "sol_to_rbh":             "rbh",
    "eth_gas_fees":           "eth",
    "forwarder":              "fwd",
    "premium_callers_signal": "fwd",
    "dexsignalcall":          "fwd",
    "bbcanalyser2":           "fwd",
    "eth_otto_group":         "fwd",
    "chain_eth":              "eth",
    "chain_rbh":              "rbh",
    "chain_sol":              "sol",
    "rpc_eth":                "eth",
    "rpc_rbh":                "rbh",
    "rpc_sol":                "sol",
    "bot_commands":           "cmd",
}

_WHY_DOWN = {
    "fwd": "Telegram userbot not logged in (no .session)",
    "eth": "ETH RPC not reachable or not configured",
    "rbh": "Robinhood RPC not reachable or not configured",
    "sol": "Solana scanner not running",
    "cmd": "TELEGRAM_BOT_TOKEN not set (get one from @BotFather)",
}


def service_states(enabled: dict[str, bool]) -> dict[str, dict]:
    """service_id -> {status, reason, depends_on} using real worker state.

    Single source of truth so the dashboard and the system page can never
    disagree about whether something is actually up.
    """
    workers = diagnostics().get("workers", {})
    out: dict[str, dict] = {}
    for sid, on in enabled.items():
        dep = _DEPENDS_ON.get(sid)
        if not on:
            status_, reason = "disabled", "turned off in Settings"
        elif dep is None:
            status_, reason = "running", ""
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
