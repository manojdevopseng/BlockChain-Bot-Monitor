"""The launchpads the monitor watches.

Adding one: write its adapter next to these, import it here, and give its
factory address a line in `.env`. Nothing else changes — the worker, the panel
and the filter tabs all read this list.

Each is one file, one line in the list below, one address in `.env` and one
Settings switch.
"""

from __future__ import annotations

from app.scanners.launchpads.base import Factory, Launch, Launchpad
from app.scanners.launchpads.flap import Flap
from app.scanners.launchpads.letscash import LetsCash
from app.scanners.launchpads.pons import Pons
from app.scanners.launchpads.pons_v2 import PonsV2
from app.scanners.launchpads.pools import Pools
from app.scanners.launchpads.virtuals import Virtuals

__all__ = ["Factory", "Launch", "Launchpad", "Pons", "PonsV2", "Flap", "Pools",
           "Virtuals", "LetsCash",
           "all_launchpads", "by_id", "by_factory"]


def all_launchpads() -> list[Launchpad]:
    """Built on demand rather than at import: each reads its factory addresses
    from scfg, and Settings can rewrite those while the app is running. A
    launchpad with no address configured is left out entirely."""
    # This order is the order of the filter tabs:
    # All, Pons, Pons V2, Flap, Pools.trade, Virtuals, LetsCash.
    return [pad for pad in (Pons(), PonsV2(), Flap(), Pools(), Virtuals(),
                            LetsCash())
            if pad.factories]


def by_id() -> dict[str, Launchpad]:
    return {pad.id: pad for pad in all_launchpads()}


def by_factory() -> dict[tuple[str, str], Launchpad]:
    """(factory address, topic0) -> the launchpad that owns it.

    The worker gets one log at a time and has to know whose it is; keying on
    both means two launchpads could share a factory address without confusion.
    """
    out: dict[tuple[str, str], Launchpad] = {}
    for pad in all_launchpads():
        for factory in pad.factories:
            out[(factory.address.lower(), factory.topic0.lower())] = pad
    return out
