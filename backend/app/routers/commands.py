"""Bot command management routes."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .. import db
from ..util import clean_list

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
async def list_commands():
    docs = await db.get_collection("commands").find({}).to_list(200)
    return {"items": clean_list(docs)}


@router.get("/stats")
async def stats():
    col = db.get_collection("commands")
    docs = await col.find({}).to_list(200)
    enabled = sum(1 for c in docs if c.get("enabled"))
    return {
        "total": len(docs),
        "enabled": enabled,
        "uses_24h": sum(c.get("usage_24h", 0) for c in docs),
    }


@router.patch("/{command}")
async def toggle_command(command: str, payload: dict = Body(...)):
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled'")
    cmd = command if command.startswith("/") else f"/{command}"
    res = await db.get_collection("commands").update_one(
        {"command": cmd}, {"$set": {"enabled": bool(payload["enabled"])}}
    )
    if not res.matched_count:
        raise HTTPException(404, f"unknown command '{cmd}'")
    return {"command": cmd, "enabled": bool(payload["enabled"])}
