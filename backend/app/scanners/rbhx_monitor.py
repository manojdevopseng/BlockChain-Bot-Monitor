"""Robinhood — X — Token Monitor.

Who is behind a Robinhood launch, read off the chain.

A Robinhood launchpad token keeps its socials in the token contract's own
`metadata()` string — description, an X link, an IPFS image. That is the only
source that works: Robinhood Chain is not on DexScreener, the explorer carries
no socials, and GMGN is unreachable from the server. It is also the best one:
our own RPC, no third party, no key, nothing to be rate-limited out of.

Measured before this was written, on live data:

    V4   60 sampled — 23 carry metadata(), and the X links are in those
    V3   60 sampled — 0
    V2   36 sampled — 0

So this is a V4 feature. V2/V3 are plain Uniswap deployments with no metadata
function at all; they are still checked when the `rbhx_v2v3` switch is on, at
the cost of one eth_call each, because a launchpad could start using them.

Only @username links are kept. A link to one post says nothing about who is
behind the token, which is the whole question here — `parse_ref` already
separates the two, so tweet links are dropped where they are classified.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Optional

import aiohttp

from app import notifier, x_client
from app.scanners import scfg as config
from app.scanners import launchpads
from app.scanners.onchain_detector import ChainSpec, DetectedToken, OnChainDetector, NATIVE_ZERO
from app.scanners.ws_provider import SubscriptionSpec
from app.scanners.slog import get_logger
from app.util import gmgn_url, ist_date_str

log = get_logger(__name__)

# Every launchpad keeps the socials somewhere different, and none of it is a
# standard — so the getters are probed in order and the first one that answers
# wins. Both of these were read off verified contracts on chain:
#
#   metadata()  Trendor-style — (description, social, image, extra)
#   socials()   PonsLauncherToken — five slots, X first, website fourth
#
# A token from a launchpad we have not met yet answers neither and is skipped.
# Adding one is a line here once its getter is known.
_METADATA_SELECTORS = (
    ("metadata()", "0x392f37e9"),
    ("socials()",  "0x53cd512a"),
)

# The link can be in any slot — one token carries it in the second field and
# another in the first — but the whole field has to BE the link, not contain
# one. A description that says "…— launched by @Trendorxyz https://x.com/
# Trendorxyz" is the launchpad crediting itself, not the token's account: that
# handle belongs to whoever deployed it and would be attached to every token
# that launchpad ever mints. A social slot holds a bare URL and nothing else.
_X_FIELD = re.compile(r"^@?(?:https?://)?(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}"
                      r"(?:/status/\d+)?/?$", re.I)

# ERC-20 Transfer(address,address,uint256). A standard, unlike everything else
# on this chain — so the dev-buy watch works the same on every launchpad.
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# Concurrent dev-buy watches. Each is one filtered subscription that stays
# quiet unless that exact wallet receives that exact token, but a socket has
# to hold them all — the same reason the gas monitor caps its own.
_MAX_DEV_WATCHES = 120
# How often the Settings switches are re-read. They were captured once at start
# and never again, so flipping the skip list, the watch list, Telegram alerts,
# verified-only or the Launchpad Monitor changed nothing until the worker was
# restarted — and only three of those restart it. One tiny query a minute buys
# a switch that means what it says.
_TOGGLE_REFRESH_SECONDS = 60

# X's own mirrors are free and rate-limited; the same handle turns up across a
# burst of copycat launches, so the lookups are serialised behind this.
_LOOKUP_GATE = asyncio.Semaphore(2)
_X_RETRIES = 2
_X_RETRY_DELAY = 3.0


def _col(name: str):
    from app import db
    return db.get_collection(name)


def decode_string_tuple(hexstr: str) -> list[str]:
    """ABI-decode a tuple of dynamic strings. [] when it is not one.

    Hand-rolled rather than pulled from web3: this is the only ABI decoding in
    the project and it is fifteen lines.
    """
    if not hexstr or hexstr == "0x":
        return []
    try:
        d = bytes.fromhex(hexstr[2:])
    except ValueError:
        return []
    out: list[str] = []
    for i in range(8):
        word = d[i * 32:(i + 1) * 32]
        if len(word) < 32:
            break
        off = int.from_bytes(word, "big")
        # A head word that is not a sane offset means we have run past the
        # head into the data — that is where the tuple ends.
        if off == 0 or off >= len(d) or off % 32:
            break
        length = int.from_bytes(d[off:off + 32], "big")
        if length > len(d):
            break
        out.append(d[off + 32:off + 32 + length].decode("utf-8", "ignore"))
    return out


# A contract answering "I do not have that function" comes back as a revert.
# Same distinction onchain.py draws for the premium checks: a revert is a valid
# answer from the chain, not an endpoint that failed.
_REVERT_WORDS = ("execution reverted", "revert", "invalid opcode",
                 "out of gas", "function selector was not recognized")


def _is_revert(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(w in msg for w in _REVERT_WORDS)


# X handles are 1-15 of [A-Za-z0-9_]; anything else out of a metadata blob is
# not a handle and must not be looked up.
_HANDLE_OK = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _proof_payload(fields: list[str]) -> dict:
    """The decoded xVerificationToken payload, or {}.

    The base64 is a payload followed by a signature, so it is read with
    raw_decode and the trailing bytes ignored — this reads the claim, it does
    not check the launchpad's signature over it.
    """
    for field in fields:
        value = (field or "").strip()
        if not value.startswith("{") or "xVerificationToken" not in value:
            continue
        try:
            token = json.loads(value).get("xVerificationToken") or ""
            raw = base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8", "ignore")
            payload, _ = json.JSONDecoder().raw_decode(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:  # noqa: BLE001
            continue        # malformed proof — treat it as absent
    return {}


def dev_wallet_from_proof(fields: list[str]) -> str:
    """The wallet the launchpad issued the proof to — the deployer's own.

    Only this wallet is watched for a dev buy. Someone determined funds a
    fresh one, and nothing on chain ties that back; this catches the launch
    that buys its own supply openly, which is the common case.
    """
    wallet = str(_proof_payload(fields).get("wallet_address") or "").strip()
    return wallet.lower() if re.match(r"^0x[0-9a-fA-F]{40}$", wallet) else ""


def handle_from_proof(fields: list[str]) -> str:
    """The handle a launchpad made the deployer prove they own, or "".

    The best source there is. Some launchpads have the deployer sign in to X
    and stamp the result into the metadata as
    {"v":1,"xVerificationToken":"<base64 JSON>"} — the payload carries
    x_handle, x_user_id and the wallet it was issued to. Anyone can paste a
    link to somebody else's account; nobody can forge this into the token they
    are launching.

    The base64 is a payload followed by a signature, so it is read with
    raw_decode and the trailing bytes ignored — this reads the claim, it does
    not check the launchpad's signature over it.
    """
    handle = str(_proof_payload(fields).get("x_handle") or "").strip().lstrip("@")
    return handle if _HANDLE_OK.match(handle) else ""


def _description(fields: list[str]) -> str:
    """The prose among the metadata fields, if there is any."""
    for field in fields:
        value = (field or "").strip()
        if value and not re.match(r"^@?(?:https?://|ipfs://|www\.)", value):
            return value
    return ""


def find_x_link(fields: list[str]) -> str:
    """The token's own X link, or "".

    A field counts only when it is nothing but the link. See _X_FIELD for why
    a link mentioned inside a description is thrown away.
    """
    for field in fields:
        value = (field or "").strip()
        if _X_FIELD.match(value):
            return value.lstrip("@")
    return ""


class RbhXMonitor:
    """Own WebSocket pool, own subscriptions, own panel.

    Deliberately not a hook inside RobinhoodScanner: this has its own endpoint
    slots and its own on/off switch, so it has to be able to run when that
    scanner is stopped and to fail without taking it down.
    """

    def __init__(self, session_factory=aiohttp.ClientSession) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        self._enabled: dict[str, bool] = {}
        self._seen: set[str] = set()
        self._v2v3 = False
        self._dev_watches = 0
        # Built in run(), not here: OnChainDetector registers its subscriptions
        # in __init__, so which of V2/V3/V4 it watches has to be decided before
        # it exists — and that comes from a switch read at start.
        self._detector: Optional[OnChainDetector] = None

    def _build(self) -> OnChainDetector:
        spec = ChainSpec(
            name               = "RBHX",
            gmgn_slug          = "robinhood",
            wss_url            = (config.RBHX_WSS_ENDPOINTS or [""])[0],
            wss_source         = lambda: list(config.RBHX_WSS_ENDPOINTS),
            chain_label        = "Robinhood — X — Token Monitor",
            alert_chat_id      = config.RBHX_ALERT_CHAT_ID,
            http_rpc           = config.RBH_RPC_HTTP,
            base_addrs         = frozenset({config.RBH_WETH.lower(), NATIVE_ZERO}),
            v2_factory         = ((config.RBH_V2_FACTORY or "").lower() or None) if self._v2v3 else None,
            v3_factory         = ((config.RBH_V3_FACTORY or "").lower() or None) if self._v2v3 else None,
            v4_poolmanager     = (config.RBH_V4_POOLMANAGER or "").lower() or None,
            explorer_token_url = config.RBH_EXPLORER_TOKEN_URL,
        )
        return OnChainDetector(spec, self._on_token)

    # ── what RPC Monitor reads ────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return bool(self._detector and self._detector.connected)

    @property
    def active_endpoint(self) -> str:
        if not self._detector:
            return ""
        return getattr(self._detector.provider, "wss_url", "") or ""

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def apply_toggles(self, enabled: dict[str, bool]) -> None:
        """Mirror the Settings switches. Called before start, on a toggle, and
        by the refresher below.

        Every switch but V2/V3 is read per message, so a change lands on the
        next launch. V2/V3 decides which subscriptions exist, so that one takes
        effect when the worker restarts — which the supervisor does for it.
        """
        self._enabled = dict(enabled)
        self._v2v3 = bool(enabled.get("rbhx_v2v3"))

    async def _toggle_refresher(self) -> None:
        """Keep the switches current without a restart.

        Only rbhx_monitor / rbhx_rpc / rbhx_v2v3 restart this worker, so the
        rest — both username lists, Telegram alerts, verified-only, the
        Launchpad Monitor — would otherwise stay at whatever they were when the
        process started.
        """
        from app import registry
        while True:
            try:
                await asyncio.sleep(_TOGGLE_REFRESH_SECONDS)
                fresh = await registry.enabled_map()
                # V2/V3 is left alone: changing it here would not add or remove
                # a subscription, and pretending otherwise is worse than
                # waiting for the restart the supervisor already does.
                v2v3 = self._v2v3
                self.apply_toggles(fresh)
                self._v2v3 = v2v3
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[RBHX] toggle refresh failed: {exc}")

    def _on(self, service: str, default: bool = True) -> bool:
        return bool(self._enabled.get(service, default))

    async def run(self) -> None:
        self._session = self._session_factory()
        self._detector = self._build()
        # Watch each launchpad's own mint event as well as pool creation. This
        # is what makes a launch visible in seconds instead of whenever its
        # bonding curve happens to end — measured at 1h 04m and 3h 33m on two
        # real tokens, and never at all for one that has not graduated.
        # The legacy address:topic:where list, still honoured so a launchpad can
        # be watched from .env before it has an adapter.
        for factory, topic, _kind, _idx in config.RBHX_LAUNCHPADS:
            self._detector.provider.add_persistent_spec(SubscriptionSpec(
                params=["logs", {"address": factory, "topics": [topic]}],
                callback=self._on_launch, label=f"RBHX-LAUNCH-{factory[:8]}",
            ))
        # And every launchpad that has one. Same socket, same callback: both
        # panels are fed by one subscription per factory, not one each.
        for pad in launchpads.all_launchpads():
            for fac in pad.factories:
                self._detector.provider.add_persistent_spec(SubscriptionSpec(
                    params=["logs", {"address": fac.address, "topics": [fac.topic0]}],
                    callback=self._on_launch,
                    label=f"PAD-{pad.id.upper()}-{fac.address[:8]}",
                ))
        which = "its own" if config.RBHX_OWN_ENDPOINTS else "Robinhood Chain's (none set yet)"
        pairs = "V2/V3/V4" if self._v2v3 else "V4 only"
        pads = launchpads.all_launchpads()
        named = ", ".join(f"{p.label}({len(p.factories)})" for p in pads) or "none"
        log.info(f"[RBHX] started — {pairs} pools, launchpads: {named}, "
                 f"on {which} endpoints ({len(config.RBHX_WSS_ENDPOINTS)} slot(s))")
        refresher = asyncio.create_task(self._toggle_refresher(),
                                        name="rbhx-toggles")
        try:
            await self._detector.run()
        finally:
            refresher.cancel()
            if self._session and not self._session.closed:
                await self._session.close()

    async def stop(self) -> None:
        if self._detector:
            self._detector.provider.stop()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── the pipeline ──────────────────────────────────────────────────────────

    async def _on_token(self, tok: DetectedToken) -> None:
        """A pool was created. Late for a launchpad token — the curve may have
        been running for hours — but it is the only signal for a launchpad we
        do not watch, and it costs nothing when the launch already came in."""
        await self._process((tok.address or "").lower(), tok.symbol or "", tok.name or "",
                            tok.dex or "v4", source="pool", tx=tok.tx_hash or "")

    async def _on_launch(self, log_obj: dict) -> None:
        """A launchpad minted a token. This is the moment the X profile goes on
        chain, so it is the moment worth reading it."""
        source_addr = (log_obj.get("address") or "").lower()
        topic0 = ((log_obj.get("topics") or [""])[0] or "").lower()
        pad = launchpads.by_factory().get((source_addr, topic0))
        # Per-launchpad switch, checked before anything is read: a launchpad
        # that is off costs no eth_call, no IPFS fetch and no X lookup.
        if pad is not None and not self._on(f"launchpad_{pad.id}"):
            return
        addr = (pad.address_from_log(log_obj, next(
                    f.token_at for f in pad.factories if f.address == source_addr))
                if pad else self._token_from_log(log_obj))
        if addr:
            # The event's own transaction IS the launch — the deployer, and
            # whatever it paid to buy in, are both in it.
            await self._process(addr, "", "", "launch", source="launch",
                                tx=log_obj.get("transactionHash") or "",
                                pad=pad, log_obj=log_obj)

    def _token_from_log(self, log_obj: dict) -> str:
        """Pull the new token's address out of whichever slot this launchpad
        put it in. Every one shapes its event differently."""
        topics = [t.lower() for t in (log_obj.get("topics") or [])]
        if not topics:
            return ""
        data = (log_obj.get("data") or "0x")[2:]
        for _addr, topic, kind, index in config.RBHX_LAUNCHPADS:
            if topics[0] != topic:
                continue
            if kind == "t":
                word = topics[index] if index < len(topics) else ""
            else:
                word = data[index * 64:(index + 1) * 64]
            if len(word.replace("0x", "")) >= 40:
                return "0x" + word.replace("0x", "")[-40:]
        return ""

    async def _process(self, addr: str, symbol: str, name: str, dex: str,
                       source: str, tx: str = "", pad=None, log_obj=None) -> None:
        if not addr or addr in self._seen:
            return          # already handled — a launch, then its pool, is one token
        self._seen.add(addr)
        # Bounded by hand: this is a plain set and the process runs for weeks.
        if len(self._seen) > 50_000:
            self._seen = set(list(self._seen)[-25_000:])
        try:
            await self._handle(addr, symbol, name, dex, source, tx, pad, log_obj)
        except Exception as exc:  # noqa: BLE001
            # Warning, not debug: a launch lost here is indistinguishable from
            # one that never had a link, and the two want opposite fixes.
            log.warning(f"[RBHX] failed on {symbol or addr[:10]}: "
                        f"{type(exc).__name__}: {exc}")

    async def _handle(self, addr: str, symbol: str, name: str, dex: str,
                      source: str, tx: str = "", pad=None, log_obj=None) -> None:
        """One launch, read once, written to whichever panels it belongs in.

        Two panels come out of this single pass, deliberately: the Launchpad
        Monitor wants every launch from a launchpad it watches, and the X
        Monitor wants only the ones with an account behind them. Reading twice
        would mean two subscriptions and two sets of eth_calls for one token.
        """
        # ── who launched it, and what they paid to buy in ────────────────────
        # First, because it is the cheapest reason to throw the launch away and
        # nothing below is worth doing for one we are dropping.
        dev, first_buy = await self._launch_buy(tx)

        # ── read the launch, through its own launchpad where we know it ──────
        launch, fields = None, []
        if pad is not None:
            launch = await pad.read(self._detector.provider, addr, log_obj or {})
            dev = launch.dev_wallet or dev
        else:
            # No adapter: the generic contract getters, which is how a pool-
            # created token or an unrecognised factory still gets read.
            for _name, selector in _METADATA_SELECTORS:
                try:
                    raw = await self._detector.provider.rpc(
                        "eth_call", [{"to": addr, "data": selector}, "latest"], timeout=8.0)
                except RuntimeError as exc:
                    # "I do not have that function" comes back as a revert, and
                    # for most tokens every one of these does — that is the
                    # answer, not a failure. Logging it as one buried the real
                    # errors under a warning per launch.
                    if _is_revert(exc):
                        continue
                    raise
                fields = decode_string_tuple(raw or "")
                if any(fields):
                    break
            if not any(fields):
                return          # nothing to read and no launchpad to ask
            dev = dev_wallet_from_proof(fields) or dev

        # A launch that takes this much of its own supply is never recorded, in
        # either panel. Decided here rather than two minutes later because on
        # this chain the deployer usually buys inside the launch transaction
        # itself (`launchAndBuy`), which is mined before any subscription of
        # ours could see it.
        if config.RBHX_DEV_BUY_MAX_ETH > 0 and first_buy > config.RBHX_DEV_BUY_MAX_ETH:
            log.info(f"[RBHX] {symbol or addr[:10]} skipped — its deployer bought "
                     f"{first_buy:.4f} ETH of it in the launch itself "
                     f"(limit {config.RBHX_DEV_BUY_MAX_ETH})")
            return

        # ── the account behind it ───────────────────────────────────────────
        proved = False
        if launch is not None:
            handle = launch.handle
        else:
            handle = handle_from_proof(fields)
            proved = bool(handle)
            if not handle:
                link = find_x_link(fields)
                ref = x_client.parse_ref(link) if link else None
                if ref is not None and ref.kind != "profile":
                    # A status link. Dropped on purpose: it identifies a post,
                    # not the account behind the launch.
                    log.info(f"[RBHX] {symbol or addr[:10]} skipped — link is a post, "
                             "not a profile")
                handle = ref.handle if (ref is not None and ref.kind == "profile") else ""

        # Straight off a launchpad event there is no name/symbol yet — the
        # event carries the address and little else.
        if not symbol:
            symbol, name = await self._name_symbol(addr)
            if launch is not None:
                symbol = symbol or launch.symbol
                name = name or launch.name

        skipped = bool(handle and self._on("rbhx_skip")
                       and await _col("rbhx_skip").find_one({"handle": handle.lower()}))
        if skipped:
            log.info(f"[RBHX] {symbol or addr[:10]} skipped — @{handle} is on the skip list")

        # ── who that account is, from the free X mirrors ────────────────────
        prof = None
        if handle and not skipped:
            async with _LOOKUP_GATE:
                prof = await x_client.fetch_profile(self._session, handle)
                for _ in range(_X_RETRIES):
                    if not prof.lookup_failed:
                        break
                    # Silence from a free mirror is not "unverified" — asking
                    # again is the difference between a real row and a missing
                    # one.
                    await asyncio.sleep(_X_RETRY_DELAY)
                    prof = await x_client.fetch_profile(self._session, handle)
            if prof.lookup_failed:
                log.info(f"[RBHX] {symbol or addr[:10]} — X gave no answer for @{handle}")

        watched = bool(handle and self._on("rbhx_watch")
                       and await _col("rbhx_watch").find_one({"handle": handle.lower()}))
        got_profile = bool(prof is not None and not prof.lookup_failed)
        now = time.time()
        described = (launch.description if launch is not None else _description(fields)) or ""
        shared = {
            "address": addr,
            "symbol": symbol or "?",
            "name": name or "",
            "dex": dex,
            # How we found it: "launch" is the launchpad's own mint event,
            # "pool" is graduation. The gap between them is the curve.
            "source": source,
            "handle": handle or None,
            "link": f"https://x.com/{handle}" if handle else None,
            "verified": bool(prof.verified) if got_profile else False,
            "verified_type": prof.verified_type if got_profile else "",
            "followers": prof.followers if got_profile else 0,
            # Two different claims, kept apart: `verified` is X's own tick,
            # `proved` is the launchpad having watched this deployer sign in.
            "proved": proved,
            # The deployer's own wallet, and what it spent buying this token.
            "dev_wallet": dev or None,
            "dev_buy_eth": round(first_buy, 4) if dev else None,
            "excerpt": (described or (prof.bio if got_profile else "") or "")[:200],
            "description": described[:400],
            "watched": watched,
            # Same field the AI page's X Links uses, so the shared <Age> cell
            # and the timestamp column read the same thing in both places.
            "open_timestamp": now,
            "found_at": now,
            "day": ist_date_str(now),
            "dt": _utc_now(),
        }

        # ── the Launchpad Monitor: every launch, account or not ──────────────
        if pad is not None and self._on("launchpad_monitor"):
            row = {**shared, "launchpad": pad.id, "launchpad_label": pad.label,
                   "website": (launch.website if launch is not None else ""),
                   "image": (launch.image if launch is not None else "")}
            await _col("launchpad_tokens").update_one({"address": addr},
                                                      {"$set": row}, upsert=True)
            from app.ws_hub import hub
            await hub.broadcast("launchpad_token",
                                {k: v for k, v in row.items() if k != "dt"})
            log.info(f"[PAD-{pad.id.upper()}] {row['symbol']}"
                     + (f" — @{handle}" if handle else " — no X account")
                     + (f" ({prof.followers:,} followers)" if got_profile else ""))

        # ── the X Monitor: only launches with an account we could read ───────
        if handle and got_profile and not skipped:
            if self._on("rbhx_verified_only", False) and not prof.verified:
                return
            await _col("rbhx_tokens").update_one({"address": addr},
                                                 {"$set": shared}, upsert=True)
            from app.ws_hub import hub
            await hub.broadcast("rbhx_token",
                                {k: v for k, v in shared.items() if k != "dt"})
            log.info(f"[RBHX] {shared['symbol']} — @{handle} [{source}] "
                     f"({prof.followers:,} followers{', verified' if prof.verified else ''}"
                     f"{', proved' if proved else ''})"
                     f"{' · WATCHED' if watched else ''}")
            await self._notify(shared)

        # Published now, judged over the next window: the launch is on the page
        # in about a second, and a deployer that buys more of its own supply
        # later is taken off it when that becomes visible.
        if dev and config.RBHX_DEV_BUY_MAX_ETH > 0:
            asyncio.create_task(
                self._watch_dev_buy(addr, dev, shared["symbol"], first_buy),
                name=f"rbhx-devbuy-{addr[:10]}")

    async def _launch_buy(self, tx: str) -> tuple[str, float]:
        """(deployer, ETH it paid) from the launch transaction.

        Its sender is the human who launched the token — checked against the
        wallet in the signed proof, where a launchpad writes one, and they
        match. So this gives the dev-buy check a wallet even on launchpads
        that write no proof at all, which is most of them.

        Its value is the buy-in: a deployer that wants supply attaches ETH to
        the same call that mints the token.
        """
        if not tx:
            return "", 0.0
        try:
            info = await self._detector.provider.rpc(
                "eth_getTransactionByHash", [tx], timeout=8.0)
        except Exception:  # noqa: BLE001
            return "", 0.0
        if not info:
            return "", 0.0
        return ((info.get("from") or "").lower(),
                int(info.get("value") or "0x0", 16) / 1e18)

    async def _watch_dev_buy(self, addr: str, dev: str, symbol: str,
                             already: float = 0.0) -> None:
        """Add up what the deployer spends on its own token, and drop the row
        if it goes past the limit.

        The launch transaction is already counted by the caller — this covers
        what comes after it, like the deployer buying from a second call
        seconds later.

        One subscription, filtered to Transfer(*, dev) on this token alone, so
        it costs nothing until that exact wallet receives that exact token. The
        ETH is the native value of the transaction that moved them — a buy
        pays; an allocation handed over by the launchpad does not, and is
        correctly counted as zero.
        """
        if self._dev_watches >= _MAX_DEV_WATCHES:
            log.warning(f"[RBHX] dev-buy watch skipped for {symbol} — {_MAX_DEV_WATCHES} already running")
            return
        provider = self._detector.provider
        # Starts from what the launch transaction already paid, so a deployer
        # that buys a little at launch and more a minute later is added up.
        spent = already
        seen_tx: set[str] = set()

        async def on_transfer(log_obj: dict) -> None:
            nonlocal spent
            tx = (log_obj.get("transactionHash") or "").lower()
            # One transaction can move tokens to the same wallet twice; its
            # value must only be counted once.
            if not tx or tx in seen_tx:
                return
            seen_tx.add(tx)
            try:
                info = await provider.rpc("eth_getTransactionByHash", [tx], timeout=8.0)
            except Exception:  # noqa: BLE001
                return
            if not info or (info.get("from") or "").lower() != dev:
                return      # tokens arriving from elsewhere are not a dev buy
            spent += int(info.get("value") or "0x0", 16) / 1e18

        sub = None
        self._dev_watches += 1
        try:
            sub = await provider.subscribe(
                ["logs", {"address": addr,
                          "topics": [_TRANSFER_TOPIC, None, "0x" + "0" * 24 + dev[2:]]}],
                on_transfer, label=f"RBHX-DEV-{addr[:8]}")
            await asyncio.sleep(config.RBHX_DEV_BUY_WINDOW)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[RBHX] dev-buy watch failed for {symbol}: {type(exc).__name__}: {exc}")
            return
        finally:
            self._dev_watches -= 1
            if sub:
                try:
                    await provider.unsubscribe(sub)
                except Exception:  # noqa: BLE001
                    pass

        if spent > config.RBHX_DEV_BUY_MAX_ETH:
            await _col("rbhx_tokens").delete_one({"address": addr})
            log.info(f"[RBHX] {symbol} removed — its deployer bought {spent:.4f} ETH of it "
                     f"(limit {config.RBHX_DEV_BUY_MAX_ETH})")
        else:
            await _col("rbhx_tokens").update_one({"address": addr},
                                                 {"$set": {"dev_buy_eth": round(spent, 4)}})

    async def _name_symbol(self, addr: str) -> tuple[str, str]:
        """ERC-20 name/symbol, for a token found before it had a pair."""
        async def one(selector: str) -> str:
            try:
                raw = await self._detector.provider.rpc(
                    "eth_call", [{"to": addr, "data": selector}, "latest"], timeout=8.0)
            except RuntimeError:
                return ""
            return (decode_string_tuple(raw or "") or [""])[0]
        symbol, name = await asyncio.gather(one("0x95d89b41"), one("0x06fdde03"))
        return symbol.strip().upper(), name.strip()

    async def _notify(self, row: dict) -> None:
        if not self._on("rbhx_telegram") or not config.DEST_RBH_X_MONITOR:
            return
        import html
        text = (
            ("👁 <b>WATCHED ACCOUNT</b>\n" if row["watched"] else "")
            + f"🔎 <b>ROBINHOOD × TOKEN</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🪙 <b>{html.escape(row['symbol'])}</b>"
            + (f" — {html.escape(row['name'])}" if row["name"] else "") + "\n"
            f"👤 @{html.escape(row['handle'])}"
            + (" ✅" if row["verified"] else "")
            + (" 🔒" if row.get("proved") else "") + "\n"
            f"👥 {row['followers']:,} followers\n"
            + (f"\n<blockquote>{html.escape(row['excerpt'][:300])}</blockquote>\n"
               if row["excerpt"] else "")
            + f"\n<code>{html.escape(row['address'])}</code>"
        )
        buttons = [b for b in (
            ("📊 GMGN", gmgn_url("rbh", row["address"])),
            ("𝕏 Profile", row["link"]),
        ) if b[1]]
        if not await notifier.send_to(config.DEST_RBH_X_MONITOR, text, buttons=buttons):
            log.warning(f"[RBHX] alert not delivered for {row['symbol']}")


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
