"""Flap — docs.flap.sh (Robinhood deployment)

Its tokens carry nothing readable on the contract: name() and symbol() and
that is all, on both the standard and the tax implementation. The socials live
in an IPFS metadata JSON whose CID sits in the launch calldata:

    {"name": …, "symbol": …, "description": …, "image": "bafkr…",
     "twitter": "https://x.com/RedditStocksRH",
     "website": "https://www.reddit.com/r/stocks/",
     "telegram": "", "github": "", "youtube": "", "debox": ""}

Which is the same shape pump.fun uses, so this reads much like the narrative
agent's own metadata fetch rather than anything new.

The deployer is the sender of the launch transaction: the Portal mints on its
behalf, so the contract's own creator is the Portal and tells us nothing.
"""

from __future__ import annotations

import json
import re

import aiohttp

from app.scanners import scfg as config
from app.scanners.launchpads.base import Factory, Launch, Launchpad
from app.scanners.slog import get_logger

log = get_logger(__name__)

# The Portal's create event: one topic, the new token in data word 1.
_TOPIC_CREATED = "0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7"

# CIDv1 (baf…) and CIDv0 (Qm…). Found by scanning the launch payload rather
# than at a fixed offset: the calldata layout differs between the standard and
# the tax token, and the CID is the one thing recognisable in both.
_CID = re.compile(rb"(baf[a-z0-9]{50,}|Qm[1-9A-HJ-NP-Za-km-z]{44})")

# Public gateways, tried in order. Volunteer infrastructure, hence more than one.
_GATEWAYS = ("https://ipfs.io/ipfs/", "https://cloudflare-ipfs.com/ipfs/",
             "https://gateway.pinata.cloud/ipfs/")
_TIMEOUT = aiohttp.ClientTimeout(total=12)


class Flap(Launchpad):
    id = "flap"
    label = "Flap"

    def __init__(self) -> None:
        self.factories = [
            Factory(address=a.lower(), topic0=_TOPIC_CREATED,
                    token_at="d1", label="Flap Portal")
            for a in config.FLAP_PORTALS if a
        ]

    async def read(self, provider, address: str, log_obj: dict) -> Launch:
        from app.scanners.launchpads.pons import handle_of
        from app.scanners.rbhx_monitor import find_x_link

        out = Launch(address=address)
        tx = log_obj.get("transactionHash") or ""
        if not tx:
            return out
        try:
            info = await provider.rpc("eth_getTransactionByHash", [tx], timeout=8.0)
        except Exception:  # noqa: BLE001
            return out
        out.dev_wallet = ((info or {}).get("from") or "").lower()
        meta = await self._metadata(provider, tx, info)
        if not meta:
            return out
        out.handle = handle_of(find_x_link([str(meta.get("twitter") or ""),
                                            str(meta.get("x") or "")]))
        out.description = str(meta.get("description") or "")
        out.website = str(meta.get("website") or "")
        out.image = str(meta.get("image") or "")
        out.name = str(meta.get("name") or "")
        out.symbol = str(meta.get("symbol") or "")
        return out

    async def _metadata(self, provider, tx: str, info) -> dict:
        """The launch's IPFS JSON, or {}."""
        payloads = [((info or {}).get("input") or "0x")[2:]]
        try:
            rec = await provider.rpc("eth_getTransactionReceipt", [tx], timeout=8.0)
            payloads += [(lg.get("data") or "0x")[2:]
                         for lg in (rec or {}).get("logs", [])]
        except Exception:  # noqa: BLE001
            pass
        cids: list[str] = []
        for blob in payloads:
            try:
                raw = bytes.fromhex(blob)
            except ValueError:
                continue
            for match in _CID.findall(raw):
                cid = match.decode()
                if cid not in cids:
                    cids.append(cid)
        # Two at most: the first CID is the metadata, the second the image, and
        # a launch that pins neither is not worth three gateway round trips.
        for cid in cids[:2]:
            body = await _fetch(cid)
            if isinstance(body, dict):
                return body
        return {}


async def _fetch(cid: str) -> dict | None:
    """The JSON behind a CID. None when it is an image or nothing answers."""
    async with aiohttp.ClientSession() as session:
        for gateway in _GATEWAYS:
            try:
                async with session.get(gateway + cid, timeout=_TIMEOUT) as resp:
                    head = await resp.content.read(4096)
            except Exception:  # noqa: BLE001
                continue
            # Images are the common case and cost nothing to rule out.
            if head[:2] in (b"\xff\xd8", b"\x89P") or head[:4] == b"RIFF":
                return None
            try:
                return json.loads(head)
            except Exception:  # noqa: BLE001
                return None
    log.debug(f"[FLAP] no gateway answered for {cid[:16]}")
    return None
