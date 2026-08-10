"""Pons — docs.ponsfamily.com

Its tokens answer socials(), a five-slot string tuple with X first and the
website fourth. Read off a verified PonsLauncherToken on chain:

    socials() -> ['https://x.com/RHC_20', '', '', 'http://rhc20.tech/', '']

Two factories, because Pons kept the old one running: a token minted by either
is a Pons token, and the dashboard filters them together.

The mint event names the deployer in topic 2 — verified against a launch whose
creation transaction came from that same address. That is what lets the dev-buy
check work here at all: Pons writes no signed proof, so there is no wallet in
the metadata to read.
"""

from __future__ import annotations

import re

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad, handle_and_kind

# TokenCreated. The same signature on both factories — the legacy one is the
# same contract at an older address.
_TOPIC_CREATED = "0xdb51ea9ad51ab453a65a4cb7e60c3cb378c9501bb002609f8f97778fb6c4235a"

_SEL_SOCIALS = "0x53cd512a"   # socials() -> (string,string,string,string,string)
_SEL_LOGO    = "0xfb7f21eb"   # logo()    -> string  (a plain URL, not IPFS)
_SEL_DESC    = "0x7284e416"   # description() -> string


# Pons hands the deployer five free-text slots and does not check them, so
# whatever ends up in one is whatever they typed. Seen on live launches: a
# token with "PEPE" in the socials slot, and another with an x.com link in the
# logo slot. A field is only used as a link when it looks like one.
_URL = re.compile(r"^(?:https?://|ipfs://|www\.)\S+$", re.I)


def _url_or_blank(value: str) -> str:
    value = (value or "").strip()
    return value if _URL.match(value) else ""


def handle_of(link: str) -> str:
    """Just the profile case, for callers that want the strict rule."""
    handle, kind = handle_and_kind(link)
    return handle if kind == "profile" else ""


class Pons(Launchpad):
    id = "pons"
    label = "Pons"
    # Profile links only, unchanged: Pons was checked end to end and left
    # alone.
    allow_post_handles = False

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=_TOPIC_CREATED,
                    token_at="t1", dev_at="t2",
                    label="Pons active" if i == 0 else "Pons legacy")
            for i, a in enumerate(config.PONS_FACTORIES) if a
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.rbhx_monitor import decode_string_tuple, find_x_link

        out = Launch(address=address,
                     dev_wallet=self.address_from_log(log_obj, "t2"))
        fields = decode_string_tuple(
            await self.eth_call(provider, address, _SEL_SOCIALS))
        if fields:
            # Read by shape, not by position: the documented order is X first
            # and website fourth, but one token carried the website in slot 1.
            out.handle, out.handle_source = self.classify(find_x_link(fields))
            out.website = next((u for u in (_url_or_blank(f) for f in fields)
                                if u and "x.com" not in u and "twitter.com" not in u), "")
        text = decode_string_tuple(await self.eth_call(provider, address, _SEL_DESC))
        if text and text[0].strip():
            out.description = text[0].strip()
        logo = decode_string_tuple(await self.eth_call(provider, address, _SEL_LOGO))
        # Same reason: one launch had its X link in the logo slot, which would
        # otherwise be handed to the panel as an image source.
        out.image = _url_or_blank(logo[0] if logo else "")
        return out
