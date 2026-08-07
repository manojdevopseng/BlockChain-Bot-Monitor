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
import re
import time
from typing import Optional

import aiohttp

from app import notifier, x_client
from app.scanners import scfg as config
from app.scanners.onchain_detector import ChainSpec, DetectedToken, OnChainDetector, NATIVE_ZERO
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
        """Mirror the Settings switches. Called before start and on change.

        Every switch but V2/V3 is read per message, so flipping one takes
        effect immediately. V2/V3 changes which subscriptions exist, so it
        takes effect when the worker is restarted — which the supervisor does
        on a toggle anyway.
        """
        self._enabled = dict(enabled)
        self._v2v3 = bool(enabled.get("rbhx_v2v3"))

    def _on(self, service: str, default: bool = True) -> bool:
        return bool(self._enabled.get(service, default))

    async def run(self) -> None:
        self._session = self._session_factory()
        self._detector = self._build()
        which = "its own" if config.RBHX_OWN_ENDPOINTS else "Robinhood Chain's (none set yet)"
        pairs = "V2/V3/V4" if self._v2v3 else "V4 only"
        log.info(f"[RBHX] started — {pairs}, on {which} endpoints "
                 f"({len(config.RBHX_WSS_ENDPOINTS)} slot(s))")
        try:
            await self._detector.run()
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def stop(self) -> None:
        if self._detector:
            self._detector.provider.stop()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── the pipeline ──────────────────────────────────────────────────────────

    async def _on_token(self, tok: DetectedToken) -> None:
        addr = (tok.address or "").lower()
        if not addr or addr in self._seen:
            return
        self._seen.add(addr)
        # Bounded by hand: this is a plain set and the process runs for weeks.
        if len(self._seen) > 50_000:
            self._seen = set(list(self._seen)[-25_000:])
        try:
            await self._handle(tok, addr)
        except Exception as exc:  # noqa: BLE001
            # Warning, not debug: a launch lost here is indistinguishable from
            # one that never had a link, and the two want opposite fixes.
            log.warning(f"[RBHX] failed on {tok.symbol or addr[:10]}: "
                        f"{type(exc).__name__}: {exc}")

    async def _handle(self, tok: DetectedToken, addr: str) -> None:
        fields: list[str] = []
        for _name, selector in _METADATA_SELECTORS:
            try:
                raw = await self._detector.provider.rpc(
                    "eth_call", [{"to": addr, "data": selector}, "latest"], timeout=8.0)
            except RuntimeError as exc:
                # "I do not have that function" comes back as a revert, and for
                # most tokens every one of these does — that is the answer, not
                # a failure. Logging it as one buried the real errors under a
                # warning per launch.
                if _is_revert(exc):
                    continue
                raise
            fields = decode_string_tuple(raw or "")
            if any(fields):
                break
        if not any(fields):
            return          # no launchpad metadata on this token
        link = find_x_link(fields)
        if not link:
            return
        ref = x_client.parse_ref(link)
        if ref.kind != "profile":
            # A status link. Dropped on purpose: it identifies a post, not the
            # account behind the launch.
            log.info(f"[RBHX] {tok.symbol or addr[:10]} skipped — link is a post, not a profile")
            return

        handle = ref.handle
        if self._on("rbhx_skip") and await _col("rbhx_skip").find_one(
                {"handle": handle.lower()}):
            log.info(f"[RBHX] {tok.symbol or addr[:10]} skipped — @{handle} is on the skip list")
            return

        async with _LOOKUP_GATE:
            prof = await x_client.fetch_profile(self._session, handle)
            for _ in range(_X_RETRIES):
                if not prof.lookup_failed:
                    break
                # Silence from a free mirror is not "unverified" — asking again
                # is the difference between a real row and a missing one.
                await asyncio.sleep(_X_RETRY_DELAY)
                prof = await x_client.fetch_profile(self._session, handle)
        if prof.lookup_failed:
            log.info(f"[RBHX] {tok.symbol or addr[:10]} dropped — X gave no answer for @{handle}")
            return
        if self._on("rbhx_verified_only", False) and not prof.verified:
            return

        watched = bool(self._on("rbhx_watch") and await _col("rbhx_watch").find_one(
            {"handle": handle.lower()}))
        now = time.time()
        row = {
            "address": addr,
            "symbol": tok.symbol or "?",
            "name": tok.name or "",
            "dex": tok.dex,
            "pair": tok.pair,
            "link": f"https://x.com/{handle}",
            "handle": handle,
            "verified": prof.verified,
            "verified_type": prof.verified_type,
            "followers": prof.followers,
            # Same field the AI page's X Links uses, so the shared <Age> cell
            # and the timestamp column read the same thing in both places.
            # The first field that is not itself a URL: Trendor puts the
            # description first, Pons puts the X link there and has none.
            "excerpt": (_description(fields) or prof.bio or "")[:200],
            "description": _description(fields)[:400],
            "watched": watched,
            "open_timestamp": now,
            "found_at": now,
            "day": ist_date_str(now),
            "dt": _utc_now(),
        }
        await _col("rbhx_tokens").update_one({"address": addr}, {"$set": row}, upsert=True)

        from app.ws_hub import hub
        await hub.broadcast("rbhx_token", {k: v for k, v in row.items() if k != "dt"})
        log.info(f"[RBHX] {row['symbol']} — @{handle} "
                 f"({prof.followers:,} followers{', verified' if prof.verified else ''})"
                 f"{' · WATCHED' if watched else ''}")
        await self._notify(row)

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
            + (" ✅" if row["verified"] else "") + "\n"
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
