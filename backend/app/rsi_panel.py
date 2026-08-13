"""The RSI settings screen on Telegram — buttons, not commands.

Typing `/rsi_bounds 30 70` works and stays, but nobody remembers the argument
order on a phone. This is the same controls as a screen: one message that gets
edited in place as you press things, so the chat does not fill with a message
per change.

    ┌ RSI Tracker ────────────────
    │ [⏸ Tracker on] [🔔 Alerts on]
    │ [ETH ✅] [BSC ✅] [RBH ✅]
    │ bounds  [−] 30 / 70 [+]
    │ timeframe [1 Sec] … [5 Min•] … [1 Day]
    │ check   [10s] [20s] [30s•] [1m]
    │ [🪙 Tokens (4)]           ← per-token screens live behind this
    └──────────────────────────────

Callback data is `rsi:<what>:<arg>`, kept short because Telegram caps it at 64
bytes and an address alone is 42 of them.

Everything writes to the collections the panel and the page already share, so
a press here shows up on the dashboard within the worker's next refresh.
"""

from __future__ import annotations

from typing import Optional

from . import db, notifier, registry
from .rsi_math import DEFAULT_HIGH, DEFAULT_LOW
from .scanners import scfg as config
from .scanners.rsi_price import chains
from .scanners.rsi_tracker import (CADENCES, CANDLE_CHOICES, DEFAULT_CADENCE,
                                   DEFAULT_CANDLES, DEFAULT_INTERVAL,
                                   INTERVAL_LABELS, INTERVALS, candles_of,
                                   period_of)
from .scanners.slog import get_logger

log = get_logger(__name__)

# How far one press moves a bound. Five is a real step on a 0-100 scale and
# still lets 30/70 be reached from anywhere in a couple of presses.
_BOUND_STEP = 5.0
# Tokens shown per screen. Telegram stops laying keyboards out sensibly well
# before this, and the page is there for a long list.
_TOKEN_LIMIT = 20
# "➕ Add token" cannot be one press: Telegram has no text field on a keyboard,
# so the chain is chosen with buttons and the address arrives as the next
# message. This remembers which chat is mid-add, and for how long — an
# abandoned one must not swallow an unrelated message an hour later.
_PENDING: dict[int, tuple[str, float]] = {}
_PENDING_SECONDS = 300.0


def _col(name: str):
    return db.get_collection(name)


async def _settings() -> dict:
    return await _col("rsi_settings").find_one({"_id": "rsi"}) or {}


def _mark(on: bool) -> str:
    return "✅" if on else "⛔"


def _span(interval: str, candles: int) -> str:
    """How far back one reading looks: the timeframe times the candle count.

    The two settings are only meaningful together — 15 candles is a quarter of
    an hour on 1 Min and fifteen hours on 1 Hour — and this is the number
    someone actually wants when choosing either.
    """
    from .scanners.rsi_tracker import INTERVALS
    total = INTERVALS.get(interval, 300) * max(1, candles - 1)
    if total < 90:
        return f"{total} seconds"
    if total < 5400:
        return f"{total / 60:.0f} minutes"
    if total < 86400 * 2:
        return f"{total / 3600:.1f} hours"
    return f"{total / 86400:.0f} days"


# ── the main screen ───────────────────────────────────────────────────────────

async def main_panel() -> tuple[str, list[list[dict]]]:
    doc = await _settings()
    enabled = await registry.enabled_map()
    low = float(doc.get("low", DEFAULT_LOW))
    high = float(doc.get("high", DEFAULT_HIGH))
    cadence = str(doc.get("cadence", DEFAULT_CADENCE))
    tokens = await _col("rsi_tokens").count_documents({})
    oversold = await _col("rsi_state").count_documents({"zone": "oversold"})
    overbought = await _col("rsi_state").count_documents({"zone": "overbought"})

    default_tf = str(doc.get("default_interval", DEFAULT_INTERVAL))
    default_candles = int(doc.get("default_candles", DEFAULT_CANDLES))
    period = period_of(default_candles)
    # The timeframe and the cadence are different things and were reading as
    # one: the timeframe is the candle RSI is computed on, the cadence is only
    # how often that sum is redone — which is what costs RPC requests.
    text = (
        f"⚙️ <b>RSI Tracker — settings</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🪙 {tokens} token(s) · {oversold} oversold · {overbought} overbought\n"
        f"📊 <b>{INTERVAL_LABELS.get(default_tf, default_tf)} × "
        f"{default_candles} candles</b> — one reading covers "
        f"{_span(default_tf, default_candles)}\n"
        f"📉 alerts when that RSI crosses <b>{low:g}</b> or <b>{high:g}</b>\n"
        f"⏱ recomputed every <b>{cadence}</b> — how often the RPC is asked\n"
        f"🔔 → {config.RSI_ALERT_CHAT_ID or 'no chat set'}\n\n"
        f"<i>The timeframe below is what a new token starts on. Each token can "
        f"sit on its own — under 🪙 Tokens.</i>"
    )

    rows: list[list[dict]] = [[
        {"text": f"Tracker {_mark(enabled.get('rsi_tracker', True))}",
         "callback_data": "rsi:tog:rsi_tracker"},
        {"text": f"Alerts {_mark(enabled.get('rsi_telegram', True))}",
         "callback_data": "rsi:tog:rsi_telegram"},
    ]]
    # A chain needs both switches; the button flips the chain one and says so.
    rows.append([{"text": f"{spec.label} {_mark(enabled.get(f'rsi_chain_{key}', True))}",
                  "callback_data": f"rsi:chain:{key}"}
                 for key, spec in chains().items()])
    rows.append([
        {"text": "➖ low", "callback_data": "rsi:low:-"},
        {"text": f"{low:g} / {high:g}", "callback_data": "rsi:noop"},
        {"text": "high ➕", "callback_data": "rsi:high:+"},
    ])
    rows.append([
        {"text": "➕ low", "callback_data": "rsi:low:+"},
        {"text": "reset 30/70", "callback_data": "rsi:bounds:reset"},
        {"text": "high ➖", "callback_data": "rsi:high:-"},
    ])
    # The RSI timeframe, three to a row — nine across is unreadable on a phone.
    keys = list(INTERVALS)
    for i in range(0, len(keys), 3):
        rows.append([{"text": INTERVAL_LABELS[k] + (" •" if k == default_tf else ""),
                      "callback_data": f"rsi:tf:{k}"} for k in keys[i:i + 3]])
    # The clock marks these as the other thing: how often, not how long.
    # How many candles a reading is made of — the other half of "how far back
    # does this look", and per token as well.
    rows.append([{"text": f"{n}{' •' if n == default_candles else ''}",
                  "callback_data": f"rsi:cn:{n}"} for n in CANDLE_CHOICES])
    rows.append([{"text": f"⏱ {c}{' •' if c == cadence else ''}",
                  "callback_data": f"rsi:cad:{c}"} for c in CADENCES])
    rows.append([{"text": "➕ Add token", "callback_data": "rsi:add"},
                 {"text": f"🪙 Tokens ({tokens})", "callback_data": "rsi:tokens"},
                 {"text": "🔄 Refresh", "callback_data": "rsi:home"}])
    return text, rows


# ── the token list, and one token's own screen ────────────────────────────────

async def token_list_panel() -> tuple[str, list[list[dict]]]:
    rows_db = await _col("rsi_tokens").find({}).sort("added_at", -1).to_list(_TOKEN_LIMIT)
    states = {}
    async for st in _col("rsi_state").find({}):
        states[(st.get("chain"), st.get("address"))] = st

    if not rows_db:
        return ("🪙 <b>No tokens tracked yet</b>\n\nAdd one with\n"
                "<code>/rsi_add &lt;chain&gt; &lt;address&gt; [interval]</code>",
                [[{"text": "⬅ Back", "callback_data": "rsi:home"}]])

    lines = ["🪙 <b>Tracked tokens</b>", "➖➖➖➖➖➖➖➖➖➖",
             "Tap one to change its timeframe or drop it."]
    keyboard: list[list[dict]] = []
    for row in rows_db:
        st = states.get((row.get("chain"), row.get("address")), {})
        value = st.get("rsi")
        shown = f"{value:.0f}" if value is not None else "…"
        name = (row.get("symbol") or row["address"][:6]).upper()
        zone = st.get("zone")
        flag = " 🔻" if zone == "oversold" else " 🔺" if zone == "overbought" else ""
        keyboard.append([{
            "text": f"{name} · {str(row.get('chain')).upper()} · "
                    f"{INTERVAL_LABELS.get(row.get('interval'), '')} · RSI {shown}{flag}",
            # The address is 42 of the 64 bytes Telegram allows, which is why
            # nothing else is packed in here.
            "callback_data": f"rsi:t:{row['address']}",
        }])
    keyboard.append([{"text": "⬅ Back", "callback_data": "rsi:home"}])
    return "\n".join(lines), keyboard


async def token_panel(address: str) -> tuple[str, list[list[dict]]]:
    row = await _col("rsi_tokens").find_one({"address": address.lower()})
    if not row:
        return await token_list_panel()
    st = await _col("rsi_state").find_one({"chain": row.get("chain"),
                                           "address": address.lower()}) or {}
    value = st.get("rsi")
    current = row.get("interval")
    mine = candles_of(row.get("period") or period_of(DEFAULT_CANDLES))
    text = (
        f"🪙 <b>{(row.get('symbol') or '?').upper()}</b> · "
        f"{str(row.get('chain')).upper()}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📊 <b>{INTERVAL_LABELS.get(current, current)} × {mine} candles</b> — "
        f"its own, covering {_span(current, mine)}\n"
        + (f"📊 RSI <b>{value:.1f}</b>" + (f" · {st.get('zone')}" if st.get("zone") else "")
           if value is not None
           else f"📊 warming up — {st.get('samples', 0)} candle(s) so far") + "\n"
        + (f"💵 {st['price']:.10g}\n" if st.get("price") else "")
        + f"\n<code>{address}</code>"
    )
    # Interval buttons, three to a row: nine of them in one row is unreadable
    # on a phone.
    keys = list(INTERVALS)
    keyboard = [[{"text": INTERVAL_LABELS[k] + (" •" if k == current else ""),
                  "callback_data": f"rsi:i:{k}:{address[:10]}"}
                 for k in keys[i:i + 3]] for i in range(0, len(keys), 3)]
    # And its own candle count, the same way its timeframe is its own. Marked
    # rather than counted out, so the row reads as one setting.
    for group in (CANDLE_CHOICES[:3], CANDLE_CHOICES[3:]):
        keyboard.append([{"text": f"{n} candles •" if n == mine else str(n),
                          "callback_data": f"rsi:cnt:{n}:{address[:10]}"}
                         for n in group])
    keyboard.append([{"text": "🗑 Stop tracking", "callback_data": f"rsi:x:{address}"},
                     {"text": "⬅ Back", "callback_data": "rsi:tokens"}])
    return text, keyboard


async def add_panel() -> tuple[str, list[list[dict]]]:
    """Which chain the address about to be sent belongs to.

    Asked rather than guessed, because the same 0x… exists on ETH, BSC and
    Robinhood and means a different token on each. "Work it out" is there too:
    it asks each chain whether it has a pool for the address.
    """
    text = ("➕ <b>Add a token</b>\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            "Pick the chain, then send me the contract address as a message.\n\n"
            "<i>Not sure which chain? Pick “Work it out” and I will ask each "
            "one whether it has a pool for that address.</i>")
    row = [{"text": spec.label, "callback_data": f"rsi:addc:{key}"}
           for key, spec in chains().items()]
    return text, [row,
                  [{"text": "🔎 Work it out", "callback_data": "rsi:addc:auto"}],
                  [{"text": "⬅ Back", "callback_data": "rsi:home"}]]


async def pending_address(chat_id, text: str) -> bool:
    """A plain message in the RSI chat, when a chain has just been chosen.

    Returns True when it was consumed as an address, so the caller knows to
    stay quiet about it otherwise.
    """
    import time as _time
    from app.scanners.rsi_commands import reply as command_reply
    entry = _PENDING.get(chat_id)
    if not entry:
        return False
    chain, at = entry
    if _time.time() - at > _PENDING_SECONDS:
        _PENDING.pop(chat_id, None)
        return False
    address = text.strip().split()[0] if text.strip() else ""
    _PENDING.pop(chat_id, None)
    # Straight through the command, so there is one implementation of adding a
    # token and one set of error messages.
    said = await command_reply("rsi_add",
                               f"/rsi_add {'' if chain == 'auto' else chain} {address}")
    await notifier.send_to(chat_id, said or "Could not add that")
    if said.startswith("✅"):
        await open_panel(chat_id)
    return True


# ── presses ───────────────────────────────────────────────────────────────────

async def handle(data: str, cb: dict) -> tuple[str, bool]:
    """One press. Returns (toast, show_alert) and redraws the screen."""
    message = cb.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    parts = data.split(":")
    what = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    toast = ""
    screen = "home"

    if what == "noop":
        return ("", False)

    if what == "tog":
        current = (await registry.enabled_map()).get(arg, True)
        await registry.set_enabled(arg, not current)
        toast = f"{'On' if not current else 'Off'}"
        log.info(f"[RSI] {arg} switched {'on' if not current else 'off'} from Telegram")

    elif what == "chain":
        key = f"rsi_chain_{arg}"
        current = (await registry.enabled_map()).get(key, True)
        await registry.set_enabled(key, not current)
        toast = f"{arg.upper()} {'on' if not current else 'off'}"

    elif what in ("low", "high"):
        doc = await _settings()
        low = float(doc.get("low", DEFAULT_LOW))
        high = float(doc.get("high", DEFAULT_HIGH))
        step = _BOUND_STEP if arg == "+" else -_BOUND_STEP
        if what == "low":
            low += step
        else:
            high += step
        if not 0 < low < high < 100:
            return ("Bounds have to stay 0 < low < high < 100", True)
        await _col("rsi_settings").update_one({"_id": "rsi"},
                                              {"$set": {"low": low, "high": high}},
                                              upsert=True)
        toast = f"{low:g} / {high:g}"

    elif what == "bounds" and arg == "reset":
        await _col("rsi_settings").update_one(
            {"_id": "rsi"}, {"$set": {"low": DEFAULT_LOW, "high": DEFAULT_HIGH}},
            upsert=True)
        toast = f"{DEFAULT_LOW:g} / {DEFAULT_HIGH:g}"

    elif what == "tf":
        # The default for new tokens. A token already on the list keeps its
        # own — that is the whole point of it being per token.
        if arg not in INTERVALS:
            return ("Unknown timeframe", True)
        await _col("rsi_settings").update_one({"_id": "rsi"},
                                              {"$set": {"default_interval": arg}},
                                              upsert=True)
        toast = f"New tokens start on {INTERVAL_LABELS[arg]}"

    elif what == "cn":
        if int(arg) not in CANDLE_CHOICES:
            return ("Unknown candle count", True)
        await _col("rsi_settings").update_one(
            {"_id": "rsi"}, {"$set": {"default_candles": int(arg),
                                      "period": period_of(int(arg))}}, upsert=True)
        toast = f"New tokens use {arg} candles"

    elif what == "cnt":
        # rsi:cnt:<count>:<address prefix> — one token's own count.
        count, prefix = int(arg), (parts[3] if len(parts) > 3 else "")
        address = await _full_address(prefix)
        if count not in CANDLE_CHOICES or not address:
            return ("Unknown candle count", True)
        await _col("rsi_tokens").update_one({"address": address},
                                            {"$set": {"period": period_of(count)}})
        toast, screen, arg = f"Now {count} candles", "token", address

    elif what == "cad":
        if arg not in CADENCES:
            return ("Unknown cadence", True)
        await _col("rsi_settings").update_one({"_id": "rsi"},
                                              {"$set": {"cadence": arg}}, upsert=True)
        toast = f"Checking every {arg}"

    elif what == "add":
        screen = "add"

    elif what == "addc":
        import time as _time
        _PENDING[chat_id] = (arg, _time.time())
        screen = "add"
        toast = ("Send me the address" if arg == "auto"
                 else f"Send me the {arg.upper()} address")
        await notifier.send_to(
            chat_id,
            f"➕ Send the contract address"
            + ("" if arg == "auto" else f" for <b>{arg.upper()}</b>")
            + " as your next message.\n"
            + f"<i>Expires in {int(_PENDING_SECONDS // 60)} minutes.</i>")

    elif what == "tokens":
        screen = "tokens"

    elif what == "t":
        screen = "token"
        arg = await _full_address(arg)

    elif what == "i":
        # rsi:i:<interval>:<address prefix> — the interval comes first because
        # the address is what has to be truncated to fit 64 bytes.
        interval, prefix = arg, (parts[3] if len(parts) > 3 else "")
        address = await _full_address(prefix)
        if interval not in INTERVALS or not address:
            return ("Unknown interval", True)
        await _col("rsi_tokens").update_one({"address": address},
                                            {"$set": {"interval": interval}})
        # Its old candles belong to another interval; the new one starts fresh.
        await _col("rsi_candles").delete_many({"address": address,
                                               "interval": {"$ne": interval}})
        toast = f"Now on {INTERVAL_LABELS[interval]} — warming up again"
        screen, arg = "token", address

    elif what == "x":
        address = arg.lower()
        await _col("rsi_tokens").delete_one({"address": address})
        await _col("rsi_candles").delete_many({"address": address})
        await _col("rsi_state").delete_many({"address": address})
        toast, screen = "Stopped tracking", "tokens"

    elif what == "home":
        screen = "home"

    if screen == "add":
        text, keyboard = await add_panel()
    elif screen == "tokens":
        text, keyboard = await token_list_panel()
    elif screen == "token":
        text, keyboard = await token_panel(arg)
    else:
        text, keyboard = await main_panel()
    await notifier.edit_panel(chat_id, message_id, text, keyboard)
    return (toast, False)


async def _full_address(prefix: str) -> str:
    """The address a truncated callback refers to. Prefixes are 10 characters,
    which is four bytes of entropy past the 0x — enough that two tracked tokens
    colliding is not a thing that happens."""
    if len(prefix) >= 42:
        return prefix.lower()
    row = await _col("rsi_tokens").find_one(
        {"address": {"$regex": f"^{prefix.lower()}", "$options": "i"}})
    return (row or {}).get("address", "")


async def open_panel(chat_id) -> Optional[int]:
    """Post a fresh settings screen into a chat."""
    text, keyboard = await main_panel()
    return await notifier.send_panel(chat_id, text, keyboard)
