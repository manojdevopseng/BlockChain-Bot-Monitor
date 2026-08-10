"""Database layer with a graceful in-memory fallback.

If MongoDB is reachable we use Motor (async). If it is NOT (e.g. Mongo not yet
installed during early development), we transparently fall back to a tiny
in-memory store that mimics the subset of the Motor collection API this app
uses — so the whole backend still runs and the dashboard is fully demoable.

Swap is invisible to routers: they always call `get_collection(name)` and use
`insert_one / find / find_one / update_one / count_documents / delete_*`.
"""

from __future__ import annotations

import itertools
import re
from typing import Optional

from .config import settings

# Set on startup by connect(); routers can read to show DB status.
DB_OK: bool = False
_backend: str = "memory"  # "mongo" | "memory"

_mongo_client = None
_mongo_db = None

# ── In-memory fallback ─────────────────────────────────────────────────────────


def _matches(doc: dict, flt: dict) -> bool:
    """Very small subset of Mongo query operators used by this app."""
    for key, cond in (flt or {}).items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                if op == "$nin" and val in opval:
                    return False
                if op == "$gte" and not (val is not None and val >= opval):
                    return False
                if op == "$lte" and not (val is not None and val <= opval):
                    return False
                if op == "$gt" and not (val is not None and val > opval):
                    return False
                if op == "$lt" and not (val is not None and val < opval):
                    return False
                if op == "$ne" and val == opval:
                    return False
                if op == "$regex":
                    flags = re.I if "i" in cond.get("$options", "") else 0
                    if val is None or not re.search(opval, str(val), flags):
                        return False
        else:
            if val != cond:
                return False
    return True


class _MemCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction: int = 1):
        self._docs.sort(key=lambda d: (d.get(key) is None, d.get(key)),
                        reverse=direction < 0)
        return self

    def skip(self, n: int):
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int):
        if n:
            self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()

    async def to_list(self, length: Optional[int] = None):
        return self._docs[:length] if length else list(self._docs)


class _MemCollection:
    _seq = itertools.count(1)

    def __init__(self, name: str):
        self.name = name
        self._docs: list[dict] = []

    async def create_index(self, *a, **k):
        return None

    async def insert_one(self, doc: dict):
        doc = dict(doc)
        doc.setdefault("_id", f"mem-{next(self._seq)}")
        self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def insert_many(self, docs: list[dict]):
        for d in docs:
            await self.insert_one(d)
        return type("R", (), {"inserted_ids": []})()

    def find(self, flt: dict | None = None, projection=None):
        docs = [dict(d) for d in self._docs if _matches(d, flt or {})]
        return _MemCursor(docs)

    async def find_one(self, flt: dict | None = None, projection=None):
        for d in self._docs:
            if _matches(d, flt or {}):
                return dict(d)
        return None

    async def update_one(self, flt: dict, update: dict, upsert: bool = False):
        for d in self._docs:
            if _matches(d, flt):
                d.update(update.get("$set", {}))
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new = dict(flt)
            new.update(update.get("$set", {}))
            await self.insert_one(new)
            return type("R", (), {"matched_count": 0, "modified_count": 0,
                                  "upserted_id": new.get("_id")})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def count_documents(self, flt: dict | None = None):
        return sum(1 for d in self._docs if _matches(d, flt or {}))

    async def delete_one(self, flt: dict):
        for i, d in enumerate(self._docs):
            if _matches(d, flt):
                self._docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    async def delete_many(self, flt: dict | None = None):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not _matches(d, flt or {})]
        return type("R", (), {"deleted_count": before - len(self._docs)})()


class _MemDB:
    def __init__(self):
        self._cols: dict[str, _MemCollection] = {}

    def __getitem__(self, name: str) -> _MemCollection:
        return self._cols.setdefault(name, _MemCollection(name))


_mem_db = _MemDB()


# ── Public API ─────────────────────────────────────────────────────────────────


async def connect() -> None:
    """Try Mongo; on any failure fall back to the in-memory store."""
    global DB_OK, _backend, _mongo_client, _mongo_db
    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        _mongo_client = AsyncIOMotorClient(
            settings.mongo_uri, serverSelectionTimeoutMS=1500
        )
        await _mongo_client.admin.command("ping")
        _mongo_db = _mongo_client[settings.mongo_db]
        DB_OK = True
        _backend = "mongo"
    except Exception as exc:  # noqa: BLE001
        DB_OK = False
        _backend = "memory"
        print(f"[db] MongoDB unavailable ({exc}); using in-memory fallback store.")


async def close() -> None:
    if _mongo_client is not None:
        _mongo_client.close()


def backend_name() -> str:
    return _backend


def get_collection(name: str):
    if _backend == "mongo" and _mongo_db is not None:
        return _mongo_db[name]
    return _mem_db[name]


# Collection -> retention setting name. `dt` is a BSON Date written on insert
# (see slog / storage_repo / seed); mongod's TTL monitor deletes expired docs
# on its own background thread, so retention costs the app nothing.
_TTL_COLLECTIONS = {
    "logs": "log_retention_days",
    "alerts": "alert_retention_days",
    "tokens": "token_retention_days",
    "gas_alerts": "alert_retention_days",
    "premium_archive": "archive_retention_days",
    # Per-day forwarder counters — tiny, but no reason to keep them longer
    # than the logs they correspond to.
    "forwarder_counters": "log_retention_days",
    # AI narrative decisions and their dedup keys. Kept as long as alerts, so
    # "why was this token ignored" stays answerable for the same window.
    "ai_decisions": "alert_retention_days",
    "ai_seen": "log_retention_days",
    # Rolling record of tokens and their X links, so the page has something to
    # paint before the next loop pass.
    "x_links": "log_retention_days",
    # Hourly counts of launches that did not become a row, and why.
    "x_drops": "log_retention_days",
    # Robinhood — X — Token Monitor: the panel and both username lists. The
    # lists expire too, on purpose — a skip you stop maintaining stops shaping
    # the feed instead of silencing an account forever.
    "rbhx_tokens": "rbhx_retention_days",
    "rbhx_skip": "rbhx_retention_days",
    "rbhx_watch": "rbhx_retention_days",
    # Robinhood Launchpad Monitor: every launch from a watched launchpad,
    # whether or not it carries an X account.
    "launchpad_tokens": "launchpad_retention_days",
}


async def _ensure_ttl(name: str, days: int) -> None:
    """Create (or re-point) the TTL index on `name`.`dt` to `days`."""
    col = get_collection(name)
    index_name = "dt_ttl"
    seconds = int(days * 86400)

    if days <= 0:
        # Retention disabled — drop the TTL index if one exists.
        try:
            await col.drop_index(index_name)
        except Exception:
            pass    # nothing to drop — retention was already off
        return

    try:
        await col.create_index("dt", name=index_name, expireAfterSeconds=seconds)
    except Exception:
        # Index exists with a different TTL — update it in place with collMod
        # (create_index cannot change expireAfterSeconds on an existing index).
        try:
            await _mongo_db.command({
                "collMod": name,
                "index": {"name": index_name, "expireAfterSeconds": seconds},
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[db] could not set TTL on {name}: {exc}")


async def ensure_indexes() -> None:
    """Create query indexes + retention (TTL) indexes.

    No-op on the in-memory backend (its create_index is a stub).
    """
    # One index per query the dashboard and the scanners actually run. Each is
    # keyed the way its query filters and sorts, so it can be served from the
    # index instead of scanning the collection.
    plan = [
        ("tokens",             "address"),                          # token lookup by CA
        ("tokens",             [("created_at", -1)]),               # newest-first list
        ("alerts",             [("created_at", -1)]),               # newest-first feed
        ("alerts",             [("type", 1), ("chain", 1)]),        # cross-chain panels
        ("logs",               [("ts", -1)]),                       # log stream
        ("services",           "id"),                               # every toggle read
        ("premium_detections", [("chain", 1), ("ts", -1)]),         # detection panels
        # Added after an audit found these queried on every request with no
        # index behind them:
        ("premium_groups",     "id"),                               # reload + toggle, ~20s
        ("gas_alerts",         [("created_at", -1)]),               # merged into Alerts
        ("premium_archive",    [("chain", 1), ("date", 1)]),        # History dropdown
        ("forwarder_counters", [("scope", 1), ("day", 1), ("key", 1)]),   # every page load
        ("chats_seen",         "id"),                               # chat-id finder
        ("commands",           "command"),                           # per-command lookup
        # The AI narrative collections. x_links is written several times a
        # minute and read newest-first on every page load, so the sort has to be
        # index-backed rather than a scan.
        ("x_links",            "address"),
        ("x_links",            [("found_at", -1)]),
        ("x_links",            [("name_key", 1), ("day", 1)]),   # per-day cap
        ("x_links",            [("day", -1)]),                   # History filter
        ("x_links",            [("og", 1), ("found_at", -1)]),   # OG section
        # The Telegram burst check: one link's launches on one day, in order.
        ("x_links",            [("link", 1), ("day", 1), ("open_timestamp", 1)]),
        ("ai_decisions",       "address"),
        ("ai_decisions",       [("at", -1)]),
        ("ai_decisions",       [("verdict", 1), ("at", -1)]),   # verdict tabs
        ("ai_decisions",       [("day", -1)]),                  # History filter
        ("ai_decisions",       [("link", 1), ("verdict", 1)]),  # the link gate
        ("ai_decisions",       [("name_key", 1), ("day", 1)]),  # the daily gate
        ("ai_decisions",       [("telegram", 1), ("at", -1)]),  # Telegram tab
        # Dashboard accounts. Looked up by name on every login, and there must
        # not be two of the same name.
        ("users",              "username"),
        # "why is this launch not in X Links" searches the mints kept on each
        # drop bucket. A multikey index, so one token is a lookup rather than a
        # scan of every bucket in the retention window.
        ("x_drops",            [("mints.mint", 1)]),
        ("rbhx_tokens",        "address"),
        ("rbhx_tokens",        [("open_timestamp", -1)]),      # newest-first panel
        ("rbhx_tokens",        [("day", -1)]),                 # History filter
        ("rbhx_tokens",        [("followers", -1)]),           # Min Followers
        # Read once per detection, so it has to be a lookup not a scan.
        ("rbhx_skip",          "handle"),
        ("rbhx_watch",         "handle"),
        ("launchpad_tokens",   "address"),
        ("launchpad_tokens",   [("open_timestamp", -1)]),            # newest-first panel
        ("launchpad_tokens",   [("launchpad", 1), ("open_timestamp", -1)]),  # per-pad tab
        ("launchpad_tokens",   [("day", -1)]),                       # History filter
        ("launchpad_tokens",   [("followers", -1)]),                 # Min Followers
    ]
    for coll, keys in plan:
        try:
            await get_collection(coll).create_index(keys)
        except Exception as exc:  # noqa: BLE001
            # Named per collection: a silent failure here shows up much later
            # as an unexplained slow query.
            print(f"[db] index on {coll} {keys} failed: {exc}")

    if _backend != "mongo":
        return
    for coll, setting in _TTL_COLLECTIONS.items():
        await _backfill_dt(coll)
        await _ensure_ttl(coll, int(getattr(settings, setting, 0)))


# Documents written before the `dt` field existed have no TTL anchor, so they
# would never expire. Derive `dt` from the float timestamp each collection
# already carries. Idempotent: only touches docs where `dt` is missing.
_TS_FIELD = {
    "logs": "ts",
    "alerts": "created_at",
    "tokens": "created_at",
    "gas_alerts": "created_at",
    "premium_archive": None,   # no float ts — fall back to "now"
    "rbhx_tokens": "open_timestamp",
    "rbhx_skip": "added_at",
    "rbhx_watch": "added_at",
    "launchpad_tokens": "open_timestamp",
}


async def _backfill_dt(name: str) -> None:
    from datetime import datetime, timezone
    col = get_collection(name)
    try:
        if not await col.count_documents({"dt": {"$exists": False}}, limit=1):
            return
        field = _TS_FIELD.get(name)
        cursor = col.find({"dt": {"$exists": False}}, {field: 1} if field else {"_id": 1})
        fixed = 0
        async for doc in cursor:
            ts = doc.get(field) if field else None
            dt = (datetime.fromtimestamp(float(ts), timezone.utc)
                  if isinstance(ts, (int, float)) and ts
                  else datetime.now(timezone.utc))
            await col.update_one({"_id": doc["_id"]}, {"$set": {"dt": dt}})
            fixed += 1
        if fixed:
            print(f"[db] backfilled dt on {fixed} pre-existing {name} document(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"[db] dt backfill skipped for {name}: {exc}")


async def retention_policy() -> dict:
    """Current retention settings + live doc counts (for /api/system)."""
    out = {}
    for coll, setting in _TTL_COLLECTIONS.items():
        days = int(getattr(settings, setting, 0))
        try:
            count = await get_collection(coll).count_documents({})
        except Exception:
            count = 0
        out[coll] = {
            "retention_days": days,
            "documents": count,
            "policy": f"auto-deleted after {days} days" if days > 0 else "kept forever",
        }
    return out
