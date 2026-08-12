"""The /rsi_* commands — the same controls the RSI page has, from Telegram.

Every one of these writes to the collections the worker reads and the panel
shows, so there is one copy of the answer and no third place for it to drift:
add a token here and it is on the page within fifteen seconds, change the
bounds on the page and the next message from here reports the new ones.

Kept out of commands.py because that module is the transport — polling,
permissions, usage counters — and this is one feature's vocabulary.
"""

from __future__ import annotations

import re
import time

from app import registry
from app.rsi_math import DEFAULT_HIGH, DEFAULT_LOW, DEFAULT_PERIOD
from app.scanners import scfg as config
from app.scanners.rsi_price import chains
from app.scanners.rsi_tracker import (CADENCES, DEFAULT_CADENCE, DEFAULT_INTERVAL,
                                      INTERVAL_LABELS, INTERVALS)
from app.util import ist_date_str

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _col(name: str):
    from app import db
    return db.get_collection(name)


def _args(text: str) -> list[str]:
    return text.split()[1:]


async def _settings() -> dict:
    return await _col("rsi_settings").find_one({"_id": "rsi"}) or {}


async def reply(cmd: str, text: str) -> str:
    """One of the /rsi_* commands, answered. Never raises — a bad argument is
    an explanation, not a stack trace in the group."""
    try:
        return await _reply(cmd, text)
    except ValueError as exc:
        return f"⚠️ {exc}"


async def _reply(cmd: str, text: str) -> str:
    args = _args(text)

    if cmd == "rsi":
        return await _status()
    if cmd == "rsi_list":
        return await _list()
    if cmd == "rsi_add":
        return await _add(args)
    if cmd == "rsi_remove":
        return await _remove(args)
    if cmd == "rsi_interval":
        return await _interval(args)
    if cmd == "rsi_bounds":
        return await _bounds(args)
    if cmd == "rsi_check":
        return await _cadence(args)
    if cmd in ("rsi_on", "rsi_off"):
        return await _switch(cmd == "rsi_on", args)
    return ""


async def _status() -> str:
    doc = await _settings()
    enabled = await registry.enabled_map()
    tokens = await _col("rsi_tokens").count_documents({})
    oversold = await _col("rsi_state").count_documents({"zone": "oversold"})
    overbought = await _col("rsi_state").count_documents({"zone": "overbought"})
    on = [c.label for k, c in chains().items() if enabled.get(f"rsi_chain_{k}", True)
          and enabled.get(f"rsi_rpc_{k}", True)]
    return (
        f"📊 <b>RSI Tracker</b> — {'on' if enabled.get('rsi_tracker', True) else 'OFF'}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🪙 {tokens} token(s) · {oversold} oversold · {overbought} overbought\n"
        f"📉 bounds {float(doc.get('low', DEFAULT_LOW)):g} / "
        f"{float(doc.get('high', DEFAULT_HIGH)):g}\n"
        f"⏱ checking every {doc.get('cadence', DEFAULT_CADENCE)} · "
        f"period {int(doc.get('period', DEFAULT_PERIOD))}\n"
        f"⛓ chains on: {', '.join(on) or 'none'}\n"
        f"🔔 alerts → {config.RSI_ALERT_CHAT_ID or 'not set'}"
    )


async def _list() -> str:
    rows = await _col("rsi_tokens").find({}).sort("added_at", -1).to_list(60)
    if not rows:
        return "No tokens tracked yet — /rsi_add &lt;chain&gt; &lt;address&gt; [interval]"
    states = {}
    async for st in _col("rsi_state").find({}):
        states[(st.get("chain"), st.get("address"))] = st
    lines = ["📋 <b>RSI — tracked tokens</b>", "➖➖➖➖➖➖➖➖➖➖"]
    for row in rows:
        st = states.get((row.get("chain"), row.get("address")), {})
        value = st.get("rsi")
        shown = f"{value:.1f}" if value is not None else "warming up"
        lines.append(f"{(row.get('symbol') or row['address'][:8]).upper()} "
                     f"[{str(row.get('chain')).upper()}] · "
                     f"{INTERVAL_LABELS.get(row.get('interval'), '')} · RSI {shown}"
                     + (f" · {st.get('zone')}" if st.get("zone") in
                        ("oversold", "overbought") else ""))
    return "\n".join(lines)


async def _add(args: list[str]) -> str:
    if len(args) < 2:
        raise ValueError("usage: /rsi_add &lt;chain&gt; &lt;address&gt; [interval]")
    chain = args[0].lower()
    if chain not in chains() and chain != "sol":
        raise ValueError(f"unknown chain '{chain}' — have "
                         f"{', '.join(list(chains()) + ['sol'])}")
    address = args[1]
    if not _ADDRESS_RE.match(address):
        raise ValueError(f"'{address}' is not a contract address")
    interval = args[2].lower() if len(args) > 2 else DEFAULT_INTERVAL
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval '{interval}' — have {', '.join(INTERVALS)}")
    now = time.time()
    await _col("rsi_tokens").update_one(
        {"chain": chain, "address": address.lower()},
        {"$set": {"chain": chain, "address": address.lower(), "interval": interval,
                  "symbol": args[3][:32] if len(args) > 3 else "",
                  "enabled": True, "added_at": now, "day": ist_date_str(now)}},
        upsert=True)
    return (f"✅ tracking <code>{address}</code> on {chain.upper()} · "
            f"{INTERVAL_LABELS[interval]}\nIt needs "
            f"{int((await _settings()).get('period', DEFAULT_PERIOD)) + 1} candles "
            f"before it reports an RSI.")


async def _remove(args: list[str]) -> str:
    if not args:
        raise ValueError("usage: /rsi_remove &lt;address&gt;")
    addr = args[0].lower()
    res = await _col("rsi_tokens").delete_one({"address": addr})
    if not res.deleted_count:
        raise ValueError(f"{args[0]} is not being tracked")
    await _col("rsi_candles").delete_many({"address": addr})
    await _col("rsi_state").delete_many({"address": addr})
    return f"🗑 stopped tracking <code>{args[0]}</code>"


async def _interval(args: list[str]) -> str:
    if len(args) < 2:
        raise ValueError("usage: /rsi_interval &lt;address&gt; &lt;interval&gt;")
    addr, interval = args[0].lower(), args[1].lower()
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval '{interval}' — have {', '.join(INTERVALS)}")
    res = await _col("rsi_tokens").update_one({"address": addr},
                                              {"$set": {"interval": interval}})
    if not res.matched_count:
        raise ValueError(f"{args[0]} is not being tracked")
    # Its old candles belong to a different interval; the new one starts fresh.
    await _col("rsi_candles").delete_many({"address": addr,
                                           "interval": {"$ne": interval}})
    return (f"⏱ <code>{args[0]}</code> now on {INTERVAL_LABELS[interval]} — "
            "warming up again")


async def _bounds(args: list[str]) -> str:
    if len(args) < 2:
        raise ValueError("usage: /rsi_bounds &lt;low&gt; &lt;high&gt;  (e.g. /rsi_bounds 30 70)")
    try:
        low, high = float(args[0]), float(args[1])
    except ValueError:
        raise ValueError("both bounds have to be numbers")
    if not 0 < low < high < 100:
        raise ValueError("bounds must be 0 &lt; low &lt; high &lt; 100")
    await _col("rsi_settings").update_one({"_id": "rsi"},
                                          {"$set": {"low": low, "high": high}},
                                          upsert=True)
    return f"📉 bounds now {low:g} / {high:g} — applies from the next check"


async def _cadence(args: list[str]) -> str:
    if not args or args[0] not in CADENCES:
        raise ValueError(f"usage: /rsi_check &lt;{' | '.join(CADENCES)}&gt;")
    await _col("rsi_settings").update_one({"_id": "rsi"},
                                          {"$set": {"cadence": args[0]}}, upsert=True)
    return f"⏱ checking every {args[0]} from now"


async def _switch(on: bool, args: list[str]) -> str:
    """The whole tracker, or one chain within it."""
    word = "on" if on else "off"
    if not args:
        await registry.set_enabled("rsi_tracker", on)
        return f"📊 RSI Tracker switched {word}"
    chain = args[0].lower()
    if chain not in chains() and chain != "sol":
        raise ValueError(f"unknown chain '{chain}' — have "
                         f"{', '.join(list(chains()) + ['sol'])}")
    await registry.set_enabled(f"rsi_chain_{chain}", on)
    return f"⛓ {chain.upper()} switched {word} for RSI"
