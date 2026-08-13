"""Pools.trade — Uniswap's liquidity launchpad

Not a bonding curve like the others: the token launches straight into a Uniswap
pool, so there is no graduation to wait for. The launcher is Uniswap's own
LiquidityLauncher, verified on the explorer, and its event says only which
token was born:

    TokenCreated(address indexed tokenAddress)

Everything else is on the token, which is Uniswap's standard UERC20 and answers
metadata() — the same four-slot tuple this monitor already knew from Trendor.
Read off two live launches:

    metadata() -> ['https://x.com/StrategyV4fun', 'https://strategyprotocol.fun/',
                   'https://…/1786388884', '']
    metadata() -> ['GMMNY', '', 'https://cdn.dexscreener.com/cms/images/…', '']

The second is why the URL guard is here as well: the slots are free text and
the first one is not always a link.

A third shape turns up in the last slot, and it is the best one: a signed
xVerificationToken, which decodes to the handle, the X user id and the wallet
that launched it. No URL is involved, so link-hunting never saw it.

The event carries no deployer, so it comes from the launch transaction's
sender. Unlike Flap that is the real one — four sampled launches came from
three different wallets, not one minting bot — so the dev-buy rule works. The
launch transaction itself is worth 0 ETH: a deployer buying their own token
does it as a swap afterwards, which is what the watcher's window is for.
"""

from __future__ import annotations

import json

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
# Same free-text problem, same guard — imported rather than copied.
from app.scanners.launchpads.pons import _url_or_blank

_TOPIC_CREATED = "0x2e2b3f61b70d2d131b2a807371103cc98d51adcaa5e9a8f9c32658ad8426e74e"

# The token factory every launcher mints through, and the reason it is watched
# as well: there is more than one LiquidityLauncher. A token launched through
# 0x0000FffF… was missed while only 0x00004c4c… was configured, and both were
# live at the time. Watching the factory means a launcher we have never heard
# of still gets caught, because the token has to be minted somewhere.
#
#   TokenCreated(address tokenAddress, (string,string,string,bytes) metadata)
#
# Nothing is indexed, so the token is data word 0. Both events fire in the same
# transaction and the worker's own dedupe keeps that to one row.
_TOPIC_TOKEN_CREATED = "0x4ef8284ecf42d4cd19686572ffd87f630858c82398911e776cb831de35eddbf4"

_SEL_METADATA = "0x392f37e9"   # metadata() -> (string,string,string,string)


class Pools(Launchpad):
    id = "pools"
    label = "Pools.trade"
    # Read like Pons: every launch is a row, and only a link to an account
    # counts as one.
    require_handle = False
    allow_post_handles = False

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=_TOPIC_CREATED,
                    token_at="t1", label="LiquidityLauncher")
            for a in config.POOLS_FACTORIES if a
        ] + [
            Factory(address=a.lower(), topic0=_TOPIC_TOKEN_CREATED,
                    token_at="d0", label="UERC20Factory")
            for a in config.POOLS_TOKEN_FACTORIES if a
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.rbhx_monitor import (decode_string_tuple, find_x_link,
                                              handle_from_proof)

        # dev_wallet is left empty on purpose: the event does not carry it, and
        # the worker falls back to the launch transaction's sender.
        out = Launch(address=address)
        fields = decode_string_tuple(
            await self.eth_call(provider, address, _SEL_METADATA))
        if not fields:
            return out
        # By shape rather than position, as everywhere else here — and then the
        # JSON slot, because some launches put their socials in there instead:
        #
        #   ['', 'https://thestartupcoin.xyz/', 'ipfs://Qma…',
        #    '{"twitter":"https://x.com/startuponrhc"}']
        #
        # find_x_link cannot see that one: it asks for a field that is nothing
        # but the link, which is what keeps a launchpad's own handle out of a
        # token's prose.
        out.handle, out.handle_source = self.classify(
            find_x_link(fields) or _x_from_json(fields))
        if not out.handle:
            # No URL anywhere — but some launches carry a signed proof instead:
            #
            #   {"v":1,"xVerificationToken":"eyJ4X2hhbmRsZSI6IlBsYXlVbmlNTU8i…"}
            #
            # which decodes to the handle, the X user id and the deployer's
            # wallet. It is the strongest claim on offer and it was being
            # dropped, because every adapter only looked for links. Three of
            # sixty recent launches with an empty Account column had one.
            proved = handle_from_proof(fields)
            if proved:
                out.handle, out.handle_source, out.proved = proved, "proof", True
        out.website = next((u for u in (_url_or_blank(f) for f in fields)
                            if u and "x.com" not in u and "twitter.com" not in u
                            and not _looks_like_image(u)), "")
        out.image = next((u for u in (_url_or_blank(f) for f in fields)
                          if _looks_like_image(u)), "")
        # Whatever prose is in there — slot 1 on most of these launches.
        out.description = next((f.strip() for f in fields
                                if f.strip() and not f.strip().startswith(("{", "http", "ipfs://"))), "")
        return out


def _x_from_json(fields: list[str]) -> str:
    """The X link out of a JSON socials blob in one of the slots, or ""."""
    for raw in fields:
        raw = (raw or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            blob = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(blob, dict):
            continue
        for key in ("twitter", "x", "twitter_url", "x_url"):
            val = str(blob.get(key) or "").strip()
            if val:
                return val
    return ""


def _looks_like_image(url: str) -> bool:
    """Slot 3 is the image and slot 2 the website, but both are free text and
    one launch had the image where the website belongs."""
    low = (url or "").lower()
    return (low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
            or "/cms/images/" in low or "blob.vercel-storage.com" in low
            or low.startswith("ipfs://"))
