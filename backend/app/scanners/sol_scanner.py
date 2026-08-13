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
import unicodedata

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

# GMGN's new-pairs feed returns an empty `launchpad` for roughly 8% of pairs
# (measured: 38 of 500). Those are not other launchpads — most are pump.fun
# tokens whose mint was not made with the vanity grinder, so the suffix is
# missing too (only 2 of the 38 ended in "pump"). Rejecting a blank label threw
# those away, and the rejection was permanent, so a label arriving later never
# helped. A missing label is now treated as unknown, not as disqualifying: the
# token goes to pending and MCap + fees decide, which is what the filter is
# actually for.
_REJECT_TTL = 30 * 60      # re-consider a rejected mint after this long

# On-chain discovery (sol_discovery) finds mints GMGN's rolling feed never
# shows us. Those still need MCap/fees, which only GMGN has.
#
# The feed is the owner wherever it can be: measured live, 78 of 80 discovered
# mints appeared in the feed within a minute or two, and reading them there
# costs nothing extra because the feed is one call for all 500. So a mint is
# only asked about individually once the feed has had its chance and still has
# not shown it. That keeps gmgn.ai traffic where it is today — every call still
# goes through the same shared client and the same rate limiter, untouched.
_ENRICH_PER_CYCLE  = 2      # per scan cycle, on top of the one feed call
_ENRICH_GRACE      = 90     # let the feed show it first, in seconds
_ENRICH_RETRY      = 300    # then re-ask this often, doubling up to the cap
_ENRICH_RETRY_MAX  = 900
# Stop chasing a discovered mint after this long. If it still has no market cap
# an hour in, it never got liquidity and can never cross the thresholds.
_DISCOVERY_MAX_AGE = 60 * 60
_DISCOVERY_MAX     = 2000

# Sanity bound on market cap. A minutes-old launchpad token reporting billions
# is bad data or a deliberate fake, not a find — live examples during testing
# read $40.7bn and $7.2bn. These only started reaching the thresholds once a
# blank launchpad label stopped being an automatic rejection, so the bound goes
# in alongside it.
_MAX_SANE_MCAP = 500_000_000


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
        # address -> when it was rejected. Time-limited so a label that shows up
        # late gets another chance; a plain set meant one bad reading blacklisted
        # a token for the life of the process.
        self._rejected: dict[str, float] = {}
        # Mints reported by on-chain discovery. lowered address -> record with
        # the real (case-sensitive) mint, the launchpad we know it came from,
        # and when we last asked GMGN about it. These are held here rather than
        # in _pending because _pending drops entries with no data, and a
        # discovered mint has none until enrichment succeeds.
        self._discovered: dict[str, dict] = {}
        self._discovery = None
        # Held on the instance, not as a local in run(), so the Settings switch
        # can stop and restart it while the scanner itself keeps running.
        self._disc_task: asyncio.Task | None = None

    def discovery_connected(self) -> bool:
        """Is the Helius on-chain discovery socket actually subscribed right now?

        Separate from whether this scanner's own task is alive — the GMGN
        rolling feed keeps working even while discovery is stuck retrying a
        429, so this is the only accurate answer to "is the SOL WSS up".
        `_discovery` is None before `_start_discovery()` runs or if
        SOL_RPC_WSS was never set — either way, not connected.
        """
        return bool(self._discovery and self._discovery.connected())

    def discovery_endpoint(self) -> str:
        """Which SOL endpoint discovery is currently on, for RPC Monitor."""
        return self._discovery.active_endpoint() if self._discovery else ""

    def kick_discovery(self) -> None:
        """Nudge discovery to retry a new endpoint right away.

        Used after a SOL_RPC_WSS endpoint is saved in Settings — an immediate
        retry, not a wait-out-the-backoff one, without restarting this whole
        scanner (which would also drop the GMGN feed's in-memory state:
        `_active`, `_pending`, `_discovered`). A no-op if discovery was never
        started (SOL_RPC_WSS blank at startup).
        """
        if self._discovery is not None:
            self._discovery.kick()

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
        self._start_discovery()
        try:
            while True:
                try:
                    await self._scan_once()
                    await self._enrich_discovered()
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
        finally:
            self._stop_discovery()

    def _start_discovery(self):
        """Start on-chain mint discovery, if it is switched on and configured.

        It runs as a child of the scanner rather than a supervisor worker of its
        own: discovery only feeds this scanner, so it should live and die with
        it. Without SOL_RPC_WSS nothing starts and behaviour is unchanged.
        """
        if self._disc_task is not None and not self._disc_task.done():
            return self._disc_task
        if not config.SOL_DISCOVERY_ENABLED:
            log.info("[SOL-RPC] discovery off — switched off in Settings")
            return None
        if not config.SOL_RPC_WSS:
            return None
        from .sol_discovery import SolDiscovery
        if not SolDiscovery.programs():
            return None
        self._discovery = SolDiscovery(self.on_mint)
        self._disc_task = asyncio.create_task(self._discovery.run(), name="sol-discovery")
        return self._disc_task

    def _stop_discovery(self) -> None:
        """Drop the socket and forget the client — the GMGN feed is untouched.

        `_discovery = None` matters as much as the cancel: `discovery_connected`
        and `discovery_endpoint` read it, and a switched-off discovery should
        report "not connected" rather than the endpoint it was last on.
        """
        if self._disc_task is not None and not self._disc_task.done():
            self._disc_task.cancel()
        self._disc_task = None
        self._discovery = None

    def sync_discovery(self) -> None:
        """Match the running socket to the `sol_onchain_discovery` switch.

        Called on every reconcile, so switching it off in Settings stops the
        subscription within the same request instead of at the next restart —
        and switching it back on redials without waiting for one either.
        """
        if config.SOL_DISCOVERY_ENABLED:
            self._start_discovery()
        elif self._disc_task is not None:
            log.info("[SOL-RPC] discovery stopping — switched off in Settings")
            self._stop_discovery()

    def on_mint(self, mint: str, launchpad: str) -> None:
        """Called by discovery for every new mint on a watched launchpad.

        Only records it — the thresholds are still MCap and fees, and those come
        from GMGN on the next cycle. Discovery answers "does this exist and who
        launched it", nothing more.
        """
        key = mint.lower()
        if key in self._done or key in self._discovered:
            return
        # An on-chain sighting outranks a stale feed reading: if the feed had
        # rejected this address on a bad label, that verdict no longer stands.
        self._rejected.pop(key, None)
        now = time.time()
        self._discovered[key] = {
            "mint":      mint,
            "launchpad": launchpad,
            "found_at":  now,
            "next_try":  now + _ENRICH_GRACE,
            "tries":     0,
        }
        if len(self._discovered) > _DISCOVERY_MAX:
            oldest = sorted(self._discovered.items(), key=lambda kv: kv[1]["found_at"])
            for addr, _rec in oldest[: len(self._discovered) - _DISCOVERY_MAX]:
                self._discovered.pop(addr, None)

    async def _enrich_discovered(self) -> None:
        """Ask GMGN for MCap/fees on mints the feed never showed us.

        Enrichment is not a gate. No data means the mint keeps its place in the
        queue and is asked about again later — it is never rejected for missing
        data, which is the failure mode this whole path exists to fix.
        """
        now = time.time()
        for addr in [a for a, r in self._discovered.items()
                     if a in self._done or now - r["found_at"] > _DISCOVERY_MAX_AGE]:
            self._discovered.pop(addr, None)

        due = sorted((kv for kv in self._discovered.items() if kv[1]["next_try"] <= now),
                     key=lambda kv: kv[1]["next_try"])
        for addr, rec in due[:_ENRICH_PER_CYCLE]:
            rec["tries"] += 1
            rec["next_try"] = now + min(_ENRICH_RETRY * 2 ** (rec["tries"] - 1),
                                        _ENRICH_RETRY_MAX)
            info = await self._client.get_web_token_info(rec["mint"], "sol")
            if not info:
                continue
            self._consider(addr, {}, info, rec["launchpad"], display_address=rec["mint"])

    async def _scan_once(self) -> None:
        pairs = await self._client.get_sol_new_pairs(limit=500)

        for pair in pairs:
            info    = pair.get("base_token_info") or {}
            address = (pair.get("base_address") or info.get("address") or "").lower()
            if not address:
                continue
            if address in self._done:
                continue
            rejected_at = self._rejected.get(address)
            if rejected_at and time.time() - rejected_at < _REJECT_TTL:
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
                    self._rejected[address] = time.time()
                    sym_raw = (info.get("symbol") or pair.get("symbol") or "?")
                    log.debug(f"[SOL] skipped {sym_raw} {address[:8]}… — "
                              f"launchpad {launchpad_raw!r} not in allowlist")
                    continue
                self._pending[address] = {}

            # The feed is showing it, so it needs no individual lookup: hand
            # ownership back and drop the discovery record. Re-checking happens
            # here every cycle for free, as part of the one 500-pair call.
            self._discovered.pop(address, None)

            self._consider(address, pair, info, launchpad_raw)

    def _consider(self, address: str, pair: dict, info: dict, launchpad_raw: str,
                  *, display_address: str | None = None) -> None:
        """Score one token against the thresholds and trigger it if it passes.

        Shared by the GMGN feed and by discovery enrichment so both apply the
        same MCap/fees rule. `display_address` keeps the real, case-sensitive
        mint on the record when the dict key is the lowered form.
        """
        symbol = _clean_symbol(info.get("symbol") or pair.get("symbol") or "")
        if not symbol:
            return

        mcap_usd = float(info.get("market_cap") or 0)
        fees_sol = _extract_fees_sol(pair, info)

        if mcap_usd > _MAX_SANE_MCAP:
            log.debug(f"[SOL] ignored {symbol} {address[:8]}… — implausible "
                      f"MCap ${mcap_usd:,.0f}")
            return

        token_data = {
            "address":        display_address or address,
            "symbol":         symbol,
            "name":           info.get("name") or symbol,
            "launchpad":      launchpad_raw,
            "fees_sol":       fees_sol,
            "mcap_usd":       mcap_usd,
            "holders":        int(info.get("holder_count") or 0),
            "liquidity_usd":  float(info.get("liquidity") or 0),
            "price_usd":      float(info.get("price") or 0),
            "open_timestamp": float(pair.get("open_timestamp")
                                   or info.get("open_timestamp") or time.time()),
        }

        is_new_pending = not self._pending.get(address)
        self._pending[address] = token_data

        mcap_ok = mcap_usd >= config.SOL_MIN_MCAP
        fees_ok = fees_sol >= config.SOL_MIN_FEES

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

def _clean_symbol(raw: str) -> str:
    """Upper-case ticker with invisible characters removed.

    Solana tickers are attacker-controlled text. Some carry bidi overrides
    (U+202E and friends) so that "‮EERHT" renders as "THREE" — the ticker is
    what cross-chain matching compares and what goes into an alert, so the
    invisible characters come out before either happens.
    """
    cleaned = "".join(c for c in raw if unicodedata.category(c) != "Cf")
    return cleaned.upper().strip()


def _matches_launchpad(launchpad: str) -> bool:
    """Is this a launchpad we watch?

    A blank label counts as yes. GMGN leaves it empty on ~8% of pairs and those
    are mostly pump.fun tokens; letting MCap and fees judge them costs nothing,
    while rejecting them lost real tokens. A label that is present and names
    some other launchpad is still rejected.
    """
    if not launchpad.strip():
        return True
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
    if not launchpad.strip(): return "Unlabelled"
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
