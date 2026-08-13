"""Pons v2 — docs.ponsfamily.com/docs/v2

A separate deployment from the v1 launcher, not a new address for it: its own
factory, curve, hook and graduation contracts, and its own event. Verified on
the explorer as PonsV2LaunchFactory, which is where the shape below comes from:

    TokenLaunched(address indexed token, address indexed curve,
                  address indexed deployer, address pairToken,
                  uint256 launchConfigId, uint256 graduationThreshold)

So the token is topic 1 and the deployer topic 3 — v1 puts the deployer in
topic 2, which is the whole difference in how the event is read.

What the token exposes is identical to v1: socials() with X first, description()
and logo(), all answered on chain. Read off three live v2 launches:

    socials() -> ['https://x.com/Git_Equity', '', '', '', '']
    logo()    -> 'ipfs://bafkreie756m7ipufssuieyb6v47roiorelqpc6w2qbonl75xxgh3…'

This is deliberately its own adapter rather than a second factory on the v1 one.
They are two launchpads that happen to share a metadata format, the panel filters
them apart, and v1 has been checked end to end and is not to be disturbed.
"""

from __future__ import annotations

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
# v1's free-text guard, imported rather than copied: Pons hands the deployer
# five unvalidated slots and both versions inherit whatever they typed.
from app.scanners.launchpads.pons import _url_or_blank

# TokenLaunched. Nothing like v1's TokenCreated — a different event on a
# different contract.
_TOPIC_LAUNCHED = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"

_SEL_SOCIALS = "0x53cd512a"   # socials() -> (string,string,string,string,string)
_SEL_LOGO    = "0xfb7f21eb"   # logo()    -> string
_SEL_DESC    = "0x7284e416"   # description() -> string


class PonsV2(Launchpad):
    id = "pons_v2"
    label = "Pons V2"
    # Same rules as v1, so the two read alike in the panel: every launch gets a
    # row, and only a link to an account counts as an account. One live v2
    # launch called itself Dentacoin and linked a Hayden Adams tweet — that is
    # exactly what the profile-only rule is for.
    require_handle = False
    allow_post_handles = False

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=_TOPIC_LAUNCHED,
                    token_at="t1", dev_at="t3", label="Pons V2 factory")
            for a in config.PONS_V2_FACTORIES if a
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.rbhx_monitor import decode_string_tuple, find_x_link

        out = Launch(address=address,
                     dev_wallet=self.address_from_log(log_obj, "t3"))
        fields = decode_string_tuple(
            await self.eth_call(provider, address, _SEL_SOCIALS))
        if fields:
            # By shape, not by position — the documented order is X first and
            # website fourth, and v1 has already been seen ignoring that.
            out.handle, out.handle_source = self.classify(find_x_link(fields))
            out.website = next((u for u in (_url_or_blank(f) for f in fields)
                                if u and "x.com" not in u and "twitter.com" not in u), "")
        # Same last resort as v1: a signed proof in one of the slots, read only
        # when no link was found.
        self.apply_proof(out, fields)
        text = decode_string_tuple(await self.eth_call(provider, address, _SEL_DESC))
        if text and text[0].strip():
            out.description = text[0].strip()
        logo = decode_string_tuple(await self.eth_call(provider, address, _SEL_LOGO))
        out.image = _url_or_blank(logo[0] if logo else "")
        return out
