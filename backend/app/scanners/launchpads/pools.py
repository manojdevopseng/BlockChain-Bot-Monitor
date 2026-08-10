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

The event carries no deployer, so it comes from the launch transaction's
sender. Unlike Flap that is the real one — four sampled launches came from
three different wallets, not one minting bot — so the dev-buy rule works. The
launch transaction itself is worth 0 ETH: a deployer buying their own token
does it as a swap afterwards, which is what the watcher's window is for.
"""

from __future__ import annotations

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
# Same free-text problem, same guard — imported rather than copied.
from app.scanners.launchpads.pons import _url_or_blank

_TOPIC_CREATED = "0x2e2b3f61b70d2d131b2a807371103cc98d51adcaa5e9a8f9c32658ad8426e74e"

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
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.rbhx_monitor import decode_string_tuple, find_x_link

        # dev_wallet is left empty on purpose: the event does not carry it, and
        # the worker falls back to the launch transaction's sender.
        out = Launch(address=address)
        fields = decode_string_tuple(
            await self.eth_call(provider, address, _SEL_METADATA))
        if not fields:
            return out
        # By shape rather than position, as everywhere else here.
        out.handle, out.handle_source = self.classify(find_x_link(fields))
        out.website = next((u for u in (_url_or_blank(f) for f in fields)
                            if u and "x.com" not in u and "twitter.com" not in u
                            and not _looks_like_image(u)), "")
        out.image = next((u for u in (_url_or_blank(f) for f in fields)
                          if _looks_like_image(u)), "")
        return out


def _looks_like_image(url: str) -> bool:
    """Slot 3 is the image and slot 2 the website, but both are free text and
    one launch had the image where the website belongs."""
    low = (url or "").lower()
    return (low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
            or "/cms/images/" in low or "blob.vercel-storage.com" in low
            or low.startswith("ipfs://"))
