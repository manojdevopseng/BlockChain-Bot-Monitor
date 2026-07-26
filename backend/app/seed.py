"""Configuration seeding.

Loads the app's *configuration* — premium groups, detection keywords, Otto hash
rules and the CallAnalyser/BuyBot trigger words — from `data/seed_data.json`
into MongoDB on first start, after which it is all user-editable from the
dashboard. Each seeder only fills a collection that is currently empty, so a
user's edits are never clobbered.

Nothing here fabricates activity. Tokens, alerts, detections, gas hits and logs
appear only when a scanner actually produces them — a dashboard row is always
something that really happened on-chain or in a Telegram group.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import db

_NOW = time.time

_SEED_FILE = Path(__file__).resolve().parent / "data" / "seed_data.json"


def _seed_data() -> dict:
    try:
        return json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _empty(name: str) -> bool:
    return (await db.get_collection(name).count_documents({})) == 0


async def seed_keywords() -> None:
    """Detection keywords — matched whole-word against premium group messages."""
    if await _empty("keywords"):
        words = _seed_data().get("detection_keywords", [])
        if words:
            await db.get_collection("keywords").insert_many([{"word": w} for w in words])


async def seed_premium_groups() -> None:
    """Premium groups the forwarder listens to. Groups added from the dashboard
    land in this same collection, so the built-ins are just a starting point."""
    if await _empty("premium_groups"):
        ids = _seed_data().get("premium_groups", [])
        if ids:
            await db.get_collection("premium_groups").insert_many([
                {"id": int(g), "name": None, "username": None,
                 "builtin": True, "enabled": True, "added_at": _NOW()}
                for g in ids
            ])


async def seed_otto_rules() -> None:
    """Otto method/function/rugger hash sets, as one config document."""
    if await _empty("otto_rules"):
        rules = _seed_data().get("otto_rules", {})
        await db.get_collection("otto_rules").insert_one({
            "_key": "default",
            "method_ids": rules.get("method_ids", []),
            "function_texts": rules.get("function_texts", []),
            "rugger_hashes": rules.get("rugger_hashes", []),
        })


async def seed_filter_keywords() -> None:
    """CallAnalyser2 / BuyBotTracker trigger phrases."""
    if await _empty("filter_keywords"):
        fk = _seed_data().get("filter_keywords", {})
        await db.get_collection("filter_keywords").insert_one({
            "_key": "default",
            "call": fk.get("call", []),
            "buybot": fk.get("buybot", []),
        })


async def seed_all() -> None:
    await seed_keywords()
    await seed_premium_groups()
    await seed_otto_rules()
    await seed_filter_keywords()
