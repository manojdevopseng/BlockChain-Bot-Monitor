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

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Launch:
    """What a launchpad can tell us about one launch. Every field optional —
    a launchpad that does not carry socials still produces a row."""
    address: str = ""
    symbol: str = ""
    name: str = ""
    # The X account behind it, when the launchpad records one.
    handle: str = ""
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
