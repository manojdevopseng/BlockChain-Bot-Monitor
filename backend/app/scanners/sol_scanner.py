"""SolanaScanner — two-stage monitor for Solana tokens.

Ported from the reference repo (core/sol_scanner.py). The only changes: config /
storage / logger imports, and `_pending` persistence now goes through the
Mongo-backed storage repo instead of a JSON file (so the run loop calls
`storage.save_pending(...)` directly instead of a thread executor + file write).
The two-stage trigger logic (launchpad filter → MCap≥40k AND fees≥1 SOL →
360-min active watch) is unchanged.
"""

import asyncio
import time

from app.scanners.gmgn_client import GMGNClient
from app.scanners import storage_repo as storage
from app.scanners import scfg as config
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger
from app import heartbeat

log = get_logger(__name__)

_DONE_MAX = 20000

_PENDING_MAX_AGE = 45 * 60
_PENDING_MAX     = 5000

_LAUNCHPAD_KEYWORDS: frozenset[str] = frozenset({
    "pump",
    "bonk",
    "bonkers",
    "bags",
})


class SolanaScanner:
    def __init__(self, client: GMGNClient) -> None:
        self._client = client

        self._done:     BoundedSet      = BoundedSet(_DONE_MAX, storage.load_set("sol_done"))
        self._pending:  dict[str, dict] = _load_pending()
        self._pending_dirty: bool = False

        now = time.time()
        self._active:   dict[str, dict] = {
            d["symbol"]: d
            for d in storage.load_watchlist()
            if isinstance(d, dict) and d.get("symbol") and d.get("expires_at", 0) > now
        }
        self._rejected: set[str]        = set()

    @property
    def active_watched_tickers(self) -> dict[str, dict]:
        now = time.time()
        return {
            sym: data
            for sym, data in self._active.items()
            if data["expires_at"] > now
        }

    async def run(self) -> None:
        log.info(
            f"[SOL] Scanner started — interval: {config.SOL_SCAN_INTERVAL}s | "
            f"launchpads: pump.fun(offchain+agent)/bonk/bonkers/bags | "
            f"trigger: MCap >= ${config.SOL_MIN_MCAP:,.0f} AND fees >= {config.SOL_MIN_FEES} SOL | "
            f"watch window: {config.SOL_WATCH_WINDOW}m"
        )
        await asyncio.sleep(3)
        while True:
            try:
                await self._scan_once()
                heartbeat.beat("sol_scan")
                self._purge_expired()
                self._purge_pending()
                self._save_watchlist()
                if self._pending_dirty:
                    self._pending_dirty = False
                    storage.save_pending(dict(self._pending))
            except asyncio.CancelledError:
                log.info("[SOL] Scanner stopped")
                return
            except Exception as exc:
                log.error(f"[SOL] Scanner error: {exc}")
            await asyncio.sleep(config.SOL_SCAN_INTERVAL)

    async def _scan_once(self) -> None:
        pairs = await self._client.get_sol_new_pairs(limit=500)

        for pair in pairs:
            info    = pair.get("base_token_info") or {}
            address = (pair.get("base_address") or info.get("address") or "").lower()
            if not address:
                continue
            if address in self._done or address in self._rejected:
                continue

            launchpad_raw = (
                pair.get("launchpad")
                or info.get("launchpad")
                or pair.get("platform")
                or info.get("platform")
                or ""
            ).lower()

            if address not in self._pending:
                if not _matches_launchpad(launchpad_raw):
                    self._rejected.add(address)
                    continue
                self._pending[address] = {}

            symbol = (info.get("symbol") or pair.get("symbol") or "").upper().strip()
            if not symbol:
                continue

            mcap_usd = float(info.get("market_cap") or 0)
            fees_sol = _extract_fees_sol(pair, info)

            token_data = {
                "address":        address,
                "symbol":         symbol,
                "name":           info.get("name") or symbol,
                "launchpad":      launchpad_raw,
                "fees_sol":       fees_sol,
                "mcap_usd":       mcap_usd,
                "holders":        int(info.get("holder_count") or 0),
                "liquidity_usd":  float(info.get("liquidity") or 0),
                "price_usd":      float(info.get("price") or 0),
                "open_timestamp": float(pair.get("open_timestamp") or time.time()),
            }

            is_new_pending = address not in self._pending or not self._pending[address]
            self._pending[address] = token_data

            mcap_ok = mcap_usd  >= config.SOL_MIN_MCAP
            fees_ok = fees_sol  >= config.SOL_MIN_FEES

            if mcap_ok and fees_ok:
                self._trigger(address, symbol, token_data)
            elif is_new_pending:
                self._pending_dirty = True

    def _trigger(self, address: str, symbol: str, token_data: dict) -> None:
        self._done.add(address)
        self._pending.pop(address, None)

        storage.save_set("sol_done", self._done)
        self._pending_dirty = True

        expires_at = time.time() + config.SOL_WATCH_WINDOW * 60
        watch_data = {**token_data, "triggered_at": time.time(), "expires_at": expires_at}

        self._active[symbol] = watch_data

        lp  = _launchpad_display(token_data["launchpad"])
        end = time.strftime("%H:%M", time.localtime(expires_at))
        log.info(
            f"[SOL] TRIGGERED  {symbol}  via {lp} | "
            f"MCap: ${token_data['mcap_usd']:,.0f} | "
            f"Fees: {token_data['fees_sol']:.3f} SOL | "
            f"Watching until {end} ({config.SOL_WATCH_WINDOW}m)"
        )

    def _save_watchlist(self) -> None:
        now = time.time()
        items = [d for d in self._active.values() if d.get("expires_at", 0) > now]
        storage.save_watchlist(items)

    def _purge_expired(self) -> None:
        now    = time.time()
        before = len(self._active)
        self._active = {
            sym: data for sym, data in self._active.items()
            if data["expires_at"] > now
        }
        removed = before - len(self._active)
        if removed:
            log.debug(f"[SOL] Purged {removed} expired watch(es) | active: {len(self._active)}")

    def _purge_pending(self) -> None:
        now    = time.time()
        cutoff = now - _PENDING_MAX_AGE
        before = len(self._pending)
        kept = {
            a: d for a, d in self._pending.items()
            if d and float(d.get("open_timestamp", 0) or 0) >= cutoff
        }
        if len(kept) > _PENDING_MAX:
            ranked = sorted(kept.items(),
                            key=lambda kv: float(kv[1].get("open_timestamp", 0) or 0),
                            reverse=True)
            kept = dict(ranked[:_PENDING_MAX])
        if len(kept) != before:
            self._pending = kept
            self._pending_dirty = True
            log.info(f"[SOL] Pending pruned {before} → {len(kept)}")


# ── Persistence helpers (Mongo-backed) ──────────────────────────────────────────

def _load_pending() -> dict:
    """Load _pending from the storage repo, pruning stale entries on load."""
    loaded = storage.load_pending() or {}
    cutoff = time.time() - _PENDING_MAX_AGE
    pruned = {
        a: d for a, d in loaded.items()
        if isinstance(d, dict) and d and float(d.get("open_timestamp", 0) or 0) >= cutoff
    }
    if pruned:
        log.debug(f"[SOL] Loaded {len(pruned)} pending token(s) "
                  f"(pruned {len(loaded) - len(pruned)} stale)")
    return pruned


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _matches_launchpad(launchpad: str) -> bool:
    return any(kw in launchpad for kw in _LAUNCHPAD_KEYWORDS)


def _launchpad_display(launchpad: str) -> str:
    if "pump" in launchpad:
        if "agent" in launchpad:
            return "Pump.fun Agent"
        if "offchain" in launchpad or "off" in launchpad:
            return "Pump.fun Offchain"
        return "Pump.fun"
    if "bonk"    in launchpad: return "Bonk"
    if "bonkers" in launchpad: return "Bonkers"
    if "bags"    in launchpad: return "Bags"
    return launchpad.title()


def _extract_fees_sol(pair: dict, info: dict) -> float:
    for src in (pair, info):
        for field in ("total_fees_sol", "fees_sol", "total_fees", "fee_sol", "fees", "fee"):
            val = src.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    for src in (pair, info):
        for field in ("volume_sol", "volume_24h_sol", "volume"):
            val = src.get(field)
            if val:
                try:
                    return float(val) * 0.0025
                except (TypeError, ValueError):
                    pass
    return 0.0
