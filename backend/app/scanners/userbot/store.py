"""Mongo reads for the userbot's runtime data.

Premium groups, trigger keywords and Otto hash rules are NOT hardcoded
anywhere: they are seeded once from app/data/seed_data.json into MongoDB and
loaded through here at start(). Groups and keywords the user adds via the
dashboard persist in the same collections, which is what keeps environment and
user data out of the userbot's own source.
"""

from __future__ import annotations

from app.util import bare_chat_id

from .common import DETECTED_MAX


def col(name: str):
    from ... import db
    return db.get_collection(name)


async def load_premium_ids() -> set:
    """Live premium group filter — built-in (seeded) + user-added, all in the
    `premium_groups` collection. Nothing hardcoded."""
    docs = await col("premium_groups").find({"enabled": {"$ne": False}}).to_list(5000)
    return {bare_chat_id(d["id"]) for d in docs if d.get("id") is not None}


async def load_group_names() -> dict:
    """bare chat id -> (title, username), straight from the collection.

    Read once at start and kept current as groups speak, so the hot path never
    has to ask Telethon who a chat is. That lookup is usually a session-cache
    hit, but "usually" on every message is what a second of latency is made
    of.
    """
    docs = await col("premium_groups").find({}).to_list(5000)
    return {bare_chat_id(d["id"]): (d.get("name") or "", d.get("username"))
            for d in docs if d.get("id") is not None}


async def load_ic_ids() -> set:
    """Groups starred for the Important Caller mirror.

    Only starred AND enabled ones: a group switched off should go quiet
    everywhere, not keep feeding one destination.
    """
    docs = await col("premium_groups").find(
        {"ic": True, "enabled": {"$ne": False}}
    ).to_list(5000)
    return {bare_chat_id(d["id"]) for d in docs if d.get("id") is not None}


async def load_otto_rules() -> tuple[set, set, set]:
    doc = await col("otto_rules").find_one({}) or {}
    return (set(doc.get("method_ids", [])),
            set(doc.get("function_texts", [])),
            set(doc.get("rugger_hashes", [])))


async def load_filter_keywords() -> tuple[list, list]:
    doc = await col("filter_keywords").find_one({}) or {}
    return (list(doc.get("call", [])), list(doc.get("buybot", [])))


async def load_detections(chain: str) -> list:
    docs = await col("premium_detections").find({"chain": chain}).to_list(DETECTED_MAX)
    docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
    return docs
