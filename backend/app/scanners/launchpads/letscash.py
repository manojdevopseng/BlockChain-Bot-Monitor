"""LetsCash — letscash.fun

Its docs name the events but not the addresses ("n/a" on every contract row):
the page reads them from the site's own config at runtime, which answers

    launchpadFactory  0x5bd1Fbe78a78fe8236fa00CF48fbEBA74ae34661
    chainId           4663 (Robinhood Chain)

and the launch event is

    TokenLaunched(address indexed token, address indexed creator,
                  bytes32 indexed poolId, uint256 configId, uint256 firstBuyIn,
                  uint256 firstBuyOut, address hook, address feeRecipient)

so the token is topic 1 and the deployer topic 2. `creator` was the launch
transaction's own sender on all eight launches sampled, which is what the
dev-buy rule needs — and here the buy-in rides inside the launch call itself
(`firstBuyIn`), so the worker's existing "what did the launch transaction pay"
check already covers it.

Reading it is Pons all over again: the token answers socials(), the same
five-slot string tuple and the same selector, plus description() and logo().
The slots are named telegram / twitter / discord / website / extra, but they
are free text and the launcher fills them in by hand, so they are read by shape
rather than by position. Off 75 live launches:

    socials() -> ['', 'https://x.com/Arenacatcoin', '', 'https://arenacat.xyz/', '']
    socials() -> ['', '', '', 'https://x.com/catinu_', '']          website slot
    socials() -> ['', 'elonmusk', '', 'https://x.com/elonmusk/status/…', '']

The last one is why post links are not accepted: 9 of those 75 had a tweet in
an X slot and they were Raydium's, Polymarket's and Elon Musk's — someone
else's tweet, not the launcher's account. Bare text like "elonmusk" is ignored
for the same reason, by handle_and_kind's URL rule.

27 of 75 named an account at all, at roughly 15 launches an hour, so every
launch is a row (require_handle stays off, as on Pons and Pools.trade) and it
is the alert path that stays quiet about the ones with nobody behind them.
"""

from __future__ import annotations

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
# The same free-text guard, imported rather than copied: these slots hold
# "elonmusk" and en.meming.world links as readily as they hold a URL.
from app.scanners.launchpads.pons import _url_or_blank

_TOPIC_LAUNCHED = "0x17091df68f499cf4e20dcfc5d42f064dd22359e785b77691c4c4ed0322608897"

_SEL_SOCIALS = "0x53cd512a"   # socials()     -> (string,string,string,string,string)
_SEL_DESC    = "0x7284e416"   # description() -> string
_SEL_LOGO    = "0xfb7f21eb"   # logo()        -> string (ipfs:// on most launches)


class LetsCash(Launchpad):
    id = "letscash"
    label = "LetsCash"
    # Read like Pons and Pools.trade: every launch is a row, and only a link to
    # the account itself counts as one.
    require_handle = False
    allow_post_handles = False

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=_TOPIC_LAUNCHED,
                    token_at="t1", dev_at="t2", label="LetsCash factory")
            for a in config.LETSCASH_FACTORIES if a
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.rbhx_monitor import decode_string_tuple, find_x_link

        out = Launch(address=address,
                     dev_wallet=self.address_from_log(log_obj, "t2"))
        fields = decode_string_tuple(
            await self.eth_call(provider, address, _SEL_SOCIALS))
        if fields:
            out.handle, out.handle_source = self.classify(find_x_link(fields))
            out.website = next((u for u in (_url_or_blank(f) for f in fields)
                                if u and "x.com" not in u and "twitter.com" not in u), "")
        # No LetsCash launch sampled carried a signed proof, but every other
        # adapter consults one and it only ever runs when the links found
        # nothing — so a launch that starts writing them is read, not missed.
        self.apply_proof(out, fields)
        text = decode_string_tuple(await self.eth_call(provider, address, _SEL_DESC))
        if text and text[0].strip():
            out.description = text[0].strip()
        logo = decode_string_tuple(await self.eth_call(provider, address, _SEL_LOGO))
        out.image = _url_or_blank(logo[0] if logo else "")
        return out
