"""Discover new Solana mints straight from the chain, not from GMGN's feed.

Why this exists: GMGN's new-pairs feed is a rolling window of the newest 500
pairs and it leaves `launchpad` empty on a chunk of them (measured live: 37 of
500). A token can be missed because it fell out of the window, or dropped
because a label was absent. Subscribing to the launchpad's own program removes
both problems — every mint the program creates is seen the moment it happens,
and the launchpad is certain because of *which* subscription it arrived on.

Division of labour, deliberately:

    discovery (here)  — the mint exists, and which launchpad made it
    enrichment (GMGN) — market cap, fees, holders

Enrichment is not a gate. A mint with no GMGN data yet waits and is asked about
again; it is never rejected for missing data. That is the whole point.

Nothing here touches the GMGN client or its rate limiter. This is a separate
WebSocket to a Solana RPC and adds no gmgn.ai traffic at all.

Off unless SOL_RPC_WSS is set. Measured on Helius, a pump.fun logs subscription
delivers ~60 messages/second, almost all of it swap traffic, of which about ten
a minute are real token creations — so check your provider's pricing before
turning this on.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import struct
from typing import Callable, Optional

from app import pump_mcap
from app.scanners import scfg as config
from app.scanners import wss_pool
from app.scanners.bounded_set import BoundedSet
from app.scanners.slog import get_logger

log = get_logger(__name__)

_SEEN_MAX = 50000
_INSTRUCTION = re.compile(r"Instruction: (\w+)")

# The instruction a launch runs. Matched exactly, never by substring: ordinary
# buys carry CreateIdempotent / CreateTokenAccount / InitializeAccount3 and
# would otherwise read as launches — they outnumber real ones by ~100:1.
_CREATE = {"create", "createv2", "createtoken", "initializemint2"}

# Anchor event discriminator for pump.fun's CreateEvent — the first 8 bytes of
# the `Program data:` blob a launch emits. Verified against the live stream:
# every mint decoded this way carried the "pump" suffix. Layout after the
# discriminator is name / symbol / uri as Borsh strings (u32 length + utf-8),
# then the mint as a 32-byte pubkey.
_PUMP_CREATE_EVENT = "1b72a94ddeeb6376"

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# Fallback path only — for a launchpad whose event layout we have not decoded,
# where the best available answer is a base58 string of mint length.
_B58_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


class SolDiscovery:
    """One WebSocket per launchpad program, reporting new mints."""

    def __init__(self, on_mint: Callable[[str, str], object]) -> None:
        # on_mint(mint_address, launchpad) — awaited if it returns a coroutine.
        self._on_mint = on_mint
        self._seen: BoundedSet = BoundedSet(_SEEN_MAX)
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._shared_pool: Optional[wss_pool.EndpointPool] = None
        # label -> is this launchpad's socket actually subscribed right now.
        # The task being alive says nothing — _watch() retries forever on a
        # rejection, so a launchpad stuck in an endless 429 loop looks exactly
        # like a healthy one to anything that only checks "is the task done".
        self._connected: dict[str, bool] = {}

    def connected(self) -> bool:
        """True if at least one watched launchpad has a live Helius subscription."""
        return any(self._connected.values())

    def _pool(self) -> wss_pool.EndpointPool:
        """One pool for every launchpad socket.

        They all dial the same provider, so one exhausted quota should be one
        alert, not one per launchpad. Built lazily rather than in __init__ so it
        reads the endpoint list at the time discovery actually starts.
        """
        if self._shared_pool is None:
            self._shared_pool = wss_pool.EndpointPool(
                "SOL-RPC",
                lambda: (config.SOL_WSS_ENDPOINTS or [config.SOL_RPC_WSS]),
                chain_label="Solana",
            )
        return self._shared_pool

    @staticmethod
    def programs() -> list[tuple[str, str]]:
        """(launchpad label, program id) from .env — nothing hardcoded here.

        The label becomes the token's launchpad downstream, so it has to be one
        the scanner already recognises: pump / bonk / bonkers / bags. A blank
        program id means that launchpad is not watched on-chain and keeps
        arriving through the GMGN feed, exactly as before.
        """
        pairs = (("pump",    config.SOL_PUMP_PROGRAM),
                 ("bonk",    config.SOL_BONK_PROGRAM),
                 ("bonkers", config.SOL_BONKERS_PROGRAM),
                 ("bags",    config.SOL_BAGS_PROGRAM))
        return [(label, pid.strip()) for label, pid in pairs if pid and pid.strip()]

    async def run(self) -> None:
        progs = self.programs()
        if not config.SOL_RPC_WSS:
            log.info("[SOL-RPC] discovery off — SOL_RPC_WSS not set")
            return
        if not progs:
            log.info("[SOL-RPC] discovery off — no launchpad program ids configured")
            return
        self._running = True
        log.info(f"[SOL-RPC] discovery starting — {len(progs)} launchpad(s): "
                 f"{', '.join(label for label, _p in progs)}")
        self._tasks = [asyncio.create_task(self._watch(label, pid), name=f"sol-disc-{label}")
                       for label, pid in progs]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            self._running = False
            for t in self._tasks:
                t.cancel()
            log.info("[SOL-RPC] discovery stopped")
            raise

    async def _watch(self, label: str, program_id: str) -> None:
        import websockets

        # Endpoints come from the same pool type ETH and Robinhood use: rotate on
        # failure, wrap back to the first, alert when all three refuse. The list
        # is re-read per attempt, so an endpoint added in Settings is dialled on
        # the next reconnect — restarting the SOL worker instead would drop the
        # PumpPortal feed and the market cap watch too.
        pool = self._pool()
        backoff = 1.0
        attempt = 0
        while self._running:
            url = pool.url_at(attempt)
            if not url:
                log.error("[SOL-RPC] no endpoint configured — add one in "
                          "Settings → RPC Endpoints")
                await asyncio.sleep(30)
                continue
            last_error: Exception | None = None
            try:
                async with websockets.connect(url, max_size=2 ** 23,
                                              ping_interval=30, ping_timeout=60) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                        "params": [{"mentions": [program_id]},
                                   {"commitment": "processed"}],
                    }))
                    ack = json.loads(await ws.recv())
                    if "result" not in ack:
                        # This used to warn and then fall through into the read
                        # loop, leaving a connected socket with no subscription
                        # on it: no mints, no errors, nothing to see. Providers
                        # rate-limit subscriptions separately from connections,
                        # so this is how a quota actually shows up here.
                        raise RuntimeError(
                            f"logsSubscribe refused: "
                            f"{(ack.get('error') or ack)!r:.200}")
                    log.info(f"[SOL-RPC] subscribed to {label} "
                             f"({program_id[:8]}…) via {wss_pool.host_of(url)}")
                    backoff = 1.0
                    self._connected[label] = True
                    await self._note_success(pool, url, label)
                    async for raw in ws:
                        await self._handle(label, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            finally:
                # Cleared on the way out for any reason — a clean cancel, a
                # dropped connection, or the exception above — so "connected"
                # never lags a socket that just died.
                self._connected[label] = False

            if last_error is not None:
                kind, detail = pool.note_failure(url, last_error)
                host = wss_pool.host_of(url)
                if kind in ("limit", "auth"):
                    # ERROR, not WARNING: warnings never reach Telegram, which is
                    # why SOL going down was the one outage nobody was told about.
                    log.error(f"[SOL-RPC] {label} endpoint {host} rejected us "
                              f"({detail}) — "
                              f"{'quota/rate limit' if kind == 'limit' else 'bad API key'}")
                else:
                    log.warning(f"[SOL-RPC] {label} socket error on {host}: {detail}")
                await self._maybe_alert(pool, label)
                # Always move on: a quota rejection will reject us again, and a
                # dropped socket is no reason to prefer the endpoint that dropped
                # it. Wrapping is what makes this a loop back to the first.
                attempt += 1
                nxt = pool.url_at(attempt)
                if nxt and nxt != url:
                    log.warning(f"[SOL-RPC] {label} switching to "
                                f"{wss_pool.host_of(nxt)} ({kind} on {host})")

            if not self._running:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    @staticmethod
    async def _note_success(pool, url: str, label: str) -> None:
        if not pool.note_success(url):
            return
        log.warning(f"[SOL-RPC] {label} recovered via {wss_pool.host_of(url)}")
        try:
            from .. import notifier
            await notifier.notify_rpc_recovered("Solana", pool.recovery_text(url))
        except Exception as exc:  # noqa: BLE001
            log.error(f"[SOL-RPC] could not send recovery alert: {exc}")

    @staticmethod
    async def _maybe_alert(pool, label: str) -> None:
        if not pool.should_alert():
            return
        body = pool.alert_text()
        log.error(f"[SOL-RPC] ALL RPC ENDPOINTS EXHAUSTED ({label}) — "
                  f"{body.splitlines()[0]}")
        try:
            from .. import notifier
            await notifier.notify_rpc_exhausted("Solana", body)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[SOL-RPC] could not send exhaustion alert: {exc}")

    async def _handle(self, label: str, raw) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        value = ((msg.get("params") or {}).get("result") or {}).get("value") or {}
        lines = value.get("logs") or []
        if not lines:
            return

        # The swaps that make up ~99% of this stream are not waste: each one
        # carries the bonding curve's reserves, which is a market cap for a
        # launch we are watching. Reading them here costs one more subscription
        # of exactly zero — the messages are already on the wire. It returns
        # immediately when nothing is being watched, which is most of the time.
        if pump_mcap.watching():
            for line in lines:
                if line.startswith("Program data: "):
                    pump_mcap.note_log_line(line)

        # Cheap prefilter first: swaps are ~99% of the stream and this runs on
        # every single message, so it has to stay as light as possible.
        if not any((m := _INSTRUCTION.search(l)) and m.group(1).lower() in _CREATE
                   for l in lines):
            return

        mint, symbol = _mint_from_logs(lines)
        if not mint or mint in self._seen:
            return
        self._seen.add(mint)
        log.info(f"[SOL-RPC] new mint via {label}: {symbol or '?'} {mint}")
        res = self._on_mint(mint, label)
        if asyncio.iscoroutine(res):
            await res


def _b58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def _decode_create_event(data: bytes) -> Optional[tuple[str, str]]:
    """(mint, symbol) out of a pump.fun CreateEvent blob, or None."""
    try:
        offset = 8
        fields = []
        for _ in range(3):                      # name, symbol, uri
            (length,) = struct.unpack_from("<I", data, offset)
            offset += 4
            if length > 512:                    # not the layout we expect
                return None
            fields.append(data[offset:offset + length].decode("utf-8", "replace"))
            offset += length
        mint = data[offset:offset + 32]
        if len(mint) != 32:
            return None
        return _b58(mint), fields[1]
    except Exception:                            # noqa: BLE001
        return None


def _mint_from_logs(lines: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Pull the new mint out of a launch transaction's logs.

    Preferred path is the launchpad's own create event, which states the mint
    outright. The base58 scan is a fallback for a launchpad whose event layout
    we have not decoded: it can be wrong, but a wrong address only fails
    enrichment and ages out — it can never fire an alert by itself.
    """
    fallback: Optional[str] = None
    known = {config.SOL_PUMP_PROGRAM, config.SOL_BONK_PROGRAM,
             config.SOL_BONKERS_PROGRAM, config.SOL_BAGS_PROGRAM}
    for line in lines:
        if "Program data:" in line:
            blob = line.split("Program data:", 1)[1].strip()
            try:
                data = base64.b64decode(blob + "=" * (-len(blob) % 4))
            except Exception:                    # noqa: BLE001
                continue
            if data[:8].hex() == _PUMP_CREATE_EVENT:
                decoded = _decode_create_event(data)
                if decoded:
                    return decoded
        elif fallback is None and "Program log:" in line:
            for cand in _B58_RE.findall(line):
                if cand not in known:
                    fallback = cand
                    break
    return fallback, None
