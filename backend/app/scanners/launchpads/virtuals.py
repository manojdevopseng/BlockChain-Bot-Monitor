"""Virtuals Protocol — whitepaper.virtuals.io

Agent tokens. The launcher is one proxy that fires two events per token:

    PreLaunched(address indexed token, address indexed pair, uint256 virtualId, …)
    Launched(address indexed token, address indexed pair, uint256 virtualId, …)

Both are watched — a token that launches straight through skips the first —
and the worker's own dedupe keeps that to one row.

The socials are the interesting part, because unlike every other launchpad here
they are not on the chain at all. `preLaunch` takes a `string[4] urls_` and it
was empty on all sixteen live launches sampled; the token contract answers no
getter; the registry and application contracts are proxies with nothing
readable. What Virtuals does have is its own API, keyed by the token address:

    socials.VERIFIED_USERNAMES.TWITTER  ->  "KarmaWallet"
    socials.VERIFIED_LINKS.TWITTER      ->  "https://x.com/KarmaWallet"

Verified is Virtuals' word, not ours: the handle is one their launch flow made
the deployer prove, which is a stronger claim than a link typed into a metadata
field. That is why these are recorded as proved.

The record appears a few seconds after the transaction — seven, on the launch
this was read from — so the fetch retries briefly rather than deciding a fresh
launch has no account.
"""

from __future__ import annotations

import asyncio

import aiohttp

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
from app.scanners.slog import get_logger

log = get_logger(__name__)

# The launcher proxy's two events. Token is topic 1 on both.
_TOPIC_PRELAUNCHED = "0xb9ee8aa6d909a3efd0bf1b0bc2bde7f998f7ad30178b0d45f9227f5382cebc8f"
_TOPIC_LAUNCHED    = "0x6ed5dc54f1333f448f2cdf7a6efc675343f880035d6f647fb7f6e9cbf8959718"

_API = "https://api.virtuals.io/api/virtuals"
_TIMEOUT = aiohttp.ClientTimeout(total=10)
# The agent record is written just after the transaction lands. Three tries
# over six seconds covers the gap without holding a launch up for long.
_TRIES = 3
_RETRY_DELAY = 2.0


class Virtuals(Launchpad):
    id = "virtuals"
    label = "Virtuals"
    # Every launch gets a row, like Pons — the panel is the record of what
    # launched, and the account columns say when there is no account.
    require_handle = False
    # A handle Virtuals verified is an account, not a link to a post, so the
    # post rule never comes up here.
    allow_post_handles = False

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=topic, token_at="t1",
                    label=f"Virtuals {name}")
            for a in config.VIRTUALS_FACTORIES if a
            for topic, name in ((_TOPIC_PRELAUNCHED, "pre-launch"),
                                (_TOPIC_LAUNCHED, "launch"))
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        out = Launch(address=address)
        record = await _agent(address)
        if not record:
            return out

        socials = record.get("socials") or {}
        usernames = socials.get("VERIFIED_USERNAMES") or {}
        links = socials.get("VERIFIED_LINKS") or {}
        handle = str(usernames.get("TWITTER") or "").strip().lstrip("@")
        if not handle:
            # Some records carry the link without the username beside it.
            from app.scanners.launchpads.base import handle_and_kind
            handle, kind = handle_and_kind(str(links.get("TWITTER") or ""))
            handle = handle if kind == "profile" else ""
        if handle:
            out.handle, out.handle_source = handle, "verified"
            out.proved = True

        out.website = str(links.get("WEBSITE") or "")
        out.description = str(record.get("description") or "")
        out.symbol = str(record.get("symbol") or "")
        out.name = str(record.get("name") or "")
        image = record.get("image")
        out.image = str((image or {}).get("url") or "") if isinstance(image, dict) else ""
        # Whoever launched it, as Virtuals recorded it. Left empty when it is
        # not an address, so the worker falls back to the transaction's sender.
        wallet = str(record.get("walletAddress") or "").strip().lower()
        if wallet.startswith("0x") and len(wallet) == 42:
            out.dev_wallet = wallet
        return out


async def _agent(address: str) -> dict:
    """The agent record for a token address, or {}.

    Asked by `preToken` — the pre-launch token, which is the address the event
    carries. `tokenAddress` answers the same for a graduated one, so both are
    tried before giving up.
    """
    async with aiohttp.ClientSession() as session:
        for attempt in range(_TRIES):
            for field in ("preToken", "tokenAddress"):
                try:
                    async with session.get(_API, params={f"filters[{field}]": address},
                                           timeout=_TIMEOUT) as resp:
                        if resp.status != 200:
                            continue
                        body = await resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.debug(f"[VIRTUALS] api {field} failed: {exc}")
                    continue
                items = body.get("data") or []
                if items:
                    return items[0]
            if attempt < _TRIES - 1:
                # The record lands a few seconds after the transaction does.
                await asyncio.sleep(_RETRY_DELAY)
    log.info(f"[VIRTUALS] no agent record for {address[:12]}… — recorded without "
             "an account")
    return {}
