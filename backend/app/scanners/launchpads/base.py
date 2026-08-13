"""One adapter per Robinhood launchpad.

Every launchpad on this chain is its own protocol. Measured on live contracts:

    Pons   PonsLauncherToken     socials()  -> five slots, X first, website 4th
                                 deployer   -> the mint event's topic 2
    Flap   Portal-minted tokens  no getter at all; the launch calldata points
                                 at an IPFS metadata JSON that holds the socials

So "read the socials" cannot be one function with a list of selectors — the
sources are different kinds of thing. Each launchpad gets a class, and adding
the next one is a new file plus a line in the registry, with nothing else
touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


def handle_and_kind(link: str) -> tuple[str, str]:
    """(@name, how it was found) out of an x.com link.

    A post link names its account in the URL, so the handle is there either
    way — what differs is the strength of the claim. Whether a launchpad
    accepts that weaker form is its own decision; see `allow_post_handles`.

    It has to be an x.com/twitter.com URL: parse_ref alone would read a bare
    word as a handle, and these metadata slots are free text — "PEPE" is not
    an account.
    """
    if not link or not re.search(r"(?:x|twitter)\.com/", link, re.I):
        return "", ""
    from app import x_client
    ref = x_client.parse_ref(link)
    if ref.kind == "profile":
        return ref.handle, "profile"
    if ref.kind == "tweet" and ref.handle:
        return ref.handle, "post"
    return "", ""


@dataclass
class Launch:
    """What a launchpad can tell us about one launch. Every field optional —
    a launchpad that does not carry socials still produces a row."""
    address: str = ""
    symbol: str = ""
    name: str = ""
    # The X account behind it, when the launchpad records one.
    handle: str = ""
    # How that handle was obtained, because the two are not equally trustworthy:
    #   proof    the launchpad watched the deployer sign in to X
    #   profile  a link to the account itself
    #   post     a link to one of its tweets — the account is named in the URL,
    #            which is weaker: anyone can link anyone's tweet
    # The X Monitor takes the first two; this panel shows all three and says
    # which is which.
    handle_source: str = ""
    # True when the launchpad made the deployer prove they own that account,
    # rather than typing a link anyone could type.
    proved: bool = False
    description: str = ""
    website: str = ""
    image: str = ""
    # The wallet that launched it, for the dev-buy check.
    dev_wallet: str = ""


@dataclass
class Factory:
    """A contract whose event announces a new token.

    `token_at` says where the new token's address sits in that event, because
    every launchpad shapes it differently: "t1" is topic 1, "d0" is data word 0.
    `dev_at` is the same for the deployer, when the event carries it.
    """
    address: str
    topic0: str
    token_at: str = "t1"
    dev_at: str = ""
    label: str = ""


class Launchpad:
    """Subclass per launchpad. `id` is what the dashboard filters on."""

    id: str = ""
    label: str = ""
    # Several factories per launchpad is normal: Pons runs an active one and a
    # legacy one, and tokens from both are still Pons.
    factories: list[Factory] = []
    # Record a launch only when it carries an X account. Per launchpad because
    # they differ in kind: Pons is a few dozen launches a day and worth seeing
    # whole, while Flap is a bot minting one every few seconds and only the
    # ones with an account behind them are worth a row.
    require_handle: bool = False
    # Whether a handle taken out of a link to one post counts. Off by default,
    # which is the strict reading: a tweet link is not a claim about who is
    # behind the launch. Flap turns it on because its twitter field is a post
    # link nearly every time, so the strict rule empties the column.
    allow_post_handles: bool = False
    # Whether this launchpad's socials can arrive after the launch does. Every
    # on-chain one is readable the moment the event fires; Virtuals keeps its
    # off chain and the record lands a minute or five later. When this is on,
    # a launch that came in without an account is read again on a schedule,
    # and the row is filled in — and alerted — if one appears.
    late_socials: bool = False

    @staticmethod
    def apply_proof(out: "Launch", fields: list[str]) -> None:
        """Last resort for the account: a signed proof instead of a link.

        Some launches carry no URL at all and put this in a metadata slot:

            {"v":1,"xVerificationToken":"eyJ4X2hhbmRsZSI6IlBsYXlVbmlNTU8i…"}

        which decodes to the handle, the X user id and the deployer's wallet.
        It is the strongest claim on offer and every adapter was blind to it,
        because they all hunted for links — a live pools.trade launch called
        UNIMMO sat in the panel with an empty Account column while its own
        contract named @PlayUniMMO.

        Only ever consulted when nothing else found a handle, so it cannot
        change what a launchpad already reads correctly.
        """
        if out.handle:
            return
        from app.scanners.rbhx_monitor import handle_from_proof
        proved = handle_from_proof(fields)
        if proved:
            out.handle, out.handle_source, out.proved = proved, "proof", True

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        """Everything this launchpad can tell us about the launch.

        `provider` is the shared WebSocket — one socket for every launchpad and
        for the X monitor, so a launch costs one subscription, not one each.
        """
        raise NotImplementedError

    # ── helpers every adapter needs ──────────────────────────────────────────

    @staticmethod
    def address_from_log(log_obj: dict, where: str) -> str:
        """Pull an address out of "t<N>" (topic) or "d<N>" (data word)."""
        if not where or len(where) < 2:
            return ""
        kind, idx = where[0].lower(), where[1:]
        if not idx.isdigit():
            return ""
        i = int(idx)
        if kind == "t":
            topics = log_obj.get("topics") or []
            word = topics[i] if i < len(topics) else ""
        else:
            data = (log_obj.get("data") or "0x")[2:]
            word = data[i * 64:(i + 1) * 64]
        word = (word or "").replace("0x", "")
        return "0x" + word[-40:] if len(word) >= 40 else ""

    def classify(self, link: str) -> tuple[str, str]:
        """The handle this launchpad is willing to take from a link."""
        handle, kind = handle_and_kind(link)
        if kind == "post" and not self.allow_post_handles:
            return "", ""
        return handle, kind

    @staticmethod
    async def eth_call(provider, to: str, selector: str) -> str:
        """A view call that treats a revert as an answer, not a failure —
        most tokens do not have most of these functions."""
        from app.scanners.rbhx_monitor import _is_revert
        try:
            return await provider.rpc("eth_call",
                                      [{"to": to, "data": selector}, "latest"],
                                      timeout=8.0) or ""
        except RuntimeError as exc:
            if _is_revert(exc):
                return ""
            raise
        except Exception:  # noqa: BLE001
            return ""
