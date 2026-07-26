"""Token discovery routes."""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Query

from .. import db, watchlist
from ..util import clean, clean_list

router = APIRouter(prefix="/api/tokens", tags=["tokens"])

# chain value stored on a token -> GMGN token-page slug
_GMGN_SLUG = {
    "eth": "eth", "ethereum": "eth",
    "rbh": "robinhood", "robinhood": "robinhood",
    "sol": "sol", "solana": "sol",
    "base": "base", "bsc": "bsc",
}


def _with_links(docs: list[dict]) -> list[dict]:
    """Attach the GMGN token URL so the UI can link each address."""
    for d in docs:
        slug = _GMGN_SLUG.get(str(d.get("chain", "")).lower())
        addr = d.get("address") or ""
        d["gmgn_url"] = f"https://gmgn.ai/{slug}/token/{addr}" if slug and addr else None
    return docs


@router.get("")
async def list_tokens(
    chain: str | None = None,
    type: str | None = None,
    q: str | None = None,
    limit: int = Query(50, le=200),
    skip: int = 0,
):
    flt: dict = {}
    if chain:
        flt["chain"] = chain
    if type:
        flt["type"] = type
    if q:
        # Escaped: an address is the obvious thing to paste in here, and "(" or
        # "." went to Mongo as a regex — a 500 on a plain search.
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{"symbol": rx}, {"address": rx}]
    col = db.get_collection("tokens")
    total = await col.count_documents(flt)
    docs = await col.find(flt).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"total": total, "items": _with_links(clean_list(docs))}


@router.get("/stats")
async def token_stats():
    col = db.get_collection("tokens")
    day_ago = time.time() - 86400
    return {
        "total": await col.count_documents({}),
        # Genuinely the last 24 hours. This used to count every document with
        # type "new" — which is all of them — and label it "24h".
        "new_24h": await col.count_documents({"created_at": {"$gte": day_ago}}),
        # The live SOL watch list, not a `tokens` type that nothing writes.
        "watching": await watchlist.count(),
    }


@router.get("/{address}")
async def get_token(address: str):
    doc = await db.get_collection("tokens").find_one({"address": address})
    if not doc:
        return {}
    return _with_links([clean(doc)])[0]
