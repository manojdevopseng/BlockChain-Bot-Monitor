"""Log stream routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db
from ..util import clean_list

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(
    level: str | None = None,
    service: str | None = None,
    q: str | None = None,
    limit: int = Query(100, le=500),
):
    flt: dict = {}
    if level:
        flt["level"] = level
    if service:
        flt["service"] = service
    if q:
        flt["message"] = {"$regex": q, "$options": "i"}
    col = db.get_collection("logs")
    total = await col.count_documents(flt)
    docs = await col.find(flt).sort("ts", -1).limit(limit).to_list(limit)
    return {"total": total, "items": clean_list(docs)}


@router.get("/stats")
async def stats():
    col = db.get_collection("logs")
    return {
        "total": await col.count_documents({}),
        "info": await col.count_documents({"level": "INFO"}),
        "warn": await col.count_documents({"level": "WARN"}),
        "error": await col.count_documents({"level": "ERROR"}),
        "debug": await col.count_documents({"level": "DEBUG"}),
    }
