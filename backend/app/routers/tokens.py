"""Token discovery routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db
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
        flt["symbol"] = {"$regex": q, "$options": "i"}
    col = db.get_collection("tokens")
    total = await col.count_documents(flt)
    docs = await col.find(flt).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"total": total, "items": _with_links(clean_list(docs))}


@router.get("/stats")
async def token_stats():
    col = db.get_collection("tokens")
    return {
        "total": await col.count_documents({}),
        "new_24h": await col.count_documents({"type": "new"}),
        "migrated": await col.count_documents({"type": "migrated"}),
        "watching": await col.count_documents({"type": "watching"}),
    }


@router.get("/{address}")
async def get_token(address: str):
    doc = await db.get_collection("tokens").find_one({"address": address})
    if not doc:
        return {}
    return _with_links([clean(doc)])[0]
