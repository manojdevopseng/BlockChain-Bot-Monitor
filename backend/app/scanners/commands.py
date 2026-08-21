"""TelegramCommands — bot command handler.

Ported from the reference repo's core/commands.py, with three changes:

  • answers from MongoDB instead of JSON state files
  • every command can be switched off individually from the dashboard; a
    disabled command stops replying AND disappears from Telegram's "/" menu
  • real usage is recorded (count, last used, failures), so the Commands page
    shows what actually happened rather than seeded numbers

Runs on the BOT token (TELEGRAM_BOT_TOKEN from @BotFather) — completely
separate from the Telethon userbot the forwarder uses, so the two never fight
over the same session and commands work even when the userbot is logged out.

Every command except /stop and /restart is read-only. Those two change bot
behaviour, so they check the sender against Telegram's own admin list for the
chat (`getChatAdministrators`) before doing anything — everyone else gets a
"admins only" reply, same as a wrong command. See ADMIN_ONLY_COMMANDS.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import aiohttp

from app.scanners import scfg as config
from app.scanners.slog import get_logger
from app.util import esc
from app import heartbeat

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# The command set this handler implements. `menu` is the description Telegram
# shows in the "/" popup. Seeded into Mongo on first run; the dashboard owns
# `enabled` and the usage counters from then on.
COMMAND_SPEC = [
    ("start",    "Bot intro and what it monitors",     "General"),
    ("help",     "List all commands",                  "General"),
    ("status",   "Running state and uptime",           "System"),
    ("services", "Which bots, chains and RPCs are on", "System"),
    ("stats",    "Detection and alert counts",         "System"),
    ("watching", "Live SOL watch list",                "Tokens"),
    ("tokens",   "Recently detected tokens",           "Tokens"),
    ("alerts",   "Recent alerts",                      "Alerts"),
    ("gas",      "Recent high-gas early buys",         "Alerts"),
    ("ping",     "Quick alive check",                  "General"),
    ("stop",     "Stop the bot — turns off all bots/chains/RPCs "
                 "(group admins only)",                "System"),
    ("restart",  "Undo /stop — turns back on exactly what it stopped "
                 "(group admins only)",                "System"),
    # The RSI tracker, controlled from here as well as the page — same
    # collections behind both, so neither can be out of date.
    # The way in on a phone: no arguments, no syntax to remember. /rsi is the
    # same screen, kept because that is the name someone looking for RSI tries.
    ("menu",         "Open the settings screen (buttons)",     "RSI Controller"),
    ("rsi",          "RSI settings screen (buttons)",          "RSI Controller"),
    ("rsi_list",     "Tracked tokens and their RSI",          "RSI Controller"),
    ("rsi_add",      "Track a token: /rsi_add <chain> <address> [interval]",
                                                              "RSI Controller"),
    ("rsi_remove",   "Stop tracking: /rsi_remove <address>",   "RSI Controller"),
    ("rsi_interval", "One token's own timeframe: /rsi_interval <address> <5m>",
                                                              "RSI Controller"),
    ("rsi_bounds",   "Set the bounds: /rsi_bounds 30 70",      "RSI Controller"),
    ("rsi_timeframe","Default timeframe for new tokens: /rsi_timeframe 5m",
                                                              "RSI Controller"),
    ("rsi_candles",  "Candles per reading: /rsi_candles [address] 31",
                                                              "RSI Controller"),
    ("rsi_check",    "How often RSI is recomputed: /rsi_check 30s", "RSI Controller"),
    ("rsi_on",       "Turn the tracker or one chain on: /rsi_on [chain]",
                                                              "RSI Controller"),
    ("rsi_off",      "Turn the tracker or one chain off: /rsi_off [chain]",
                                                              "RSI Controller"),
    # The Market Cap Alert section, the same way: a button screen for the
    # phone, commands for when the address is already in the clipboard. /menu
    # opens this one in its own chat and the RSI one in RSI's — each group gets
    # the screen its alerts belong to.
    ("mcap",         "Market Cap settings screen (buttons)",   "Market Cap Alert"),
    # The checker: pick a chain, send an address, get its market cap. Nothing
    # is watched or stored, so it is its own command rather than a mode of the
    # one above.
    ("mc",           "Check a market cap: pick chain, send address",
                                                              "Market Cap Alert"),
    ("mcap_list",    "Watched tokens and their market cap",    "Market Cap Alert"),
    ("mcap_add",     "Watch a token: /mcap_add <chain> <address> <target>",
                                                              "Market Cap Alert"),
    ("mcap_target",  "Change a target: /mcap_target <address> 250k",
                                                              "Market Cap Alert"),
    ("mcap_remove",  "Stop watching: /mcap_remove <address>",  "Market Cap Alert"),
    ("mcap_check",   "How often it is checked: /mcap_check 15s",
                                                              "Market Cap Alert"),
    ("mcap_on",      "Turn the watcher or one chain on: /mcap_on [chain]",
                                                              "Market Cap Alert"),
    ("mcap_off",     "Turn the watcher or one chain off: /mcap_off [chain]",
                                                              "Market Cap Alert"),
]

# Checked against Telegram's own admin list for the chat, not a hardcoded user
# id — whoever the group actually makes an admin can use these, and losing
# admin status in the group revokes it here too, automatically.
ADMIN_ONLY_COMMANDS = {"stop", "restart"}

# How long a chat's admin list is trusted before asking Telegram again. Long
# enough that this cannot become a de-facto rate limit on getChatAdministrators
# if someone spams the command; short enough that a promotion/demotion in the
# group takes effect within about two minutes rather than needing a restart.
_ADMIN_CACHE_TTL = 120.0


# The screens that are just a read of something and can therefore be redrawn
# in place: a Refresh button and a way to any of the others. Everything else —
# the RSI and Market Cap panels, /stop, /restart — has its own buttons or is an
# action rather than a view.
_BUTTON_SCREENS = ("help", "start", "status", "services", "stats",
                   "watching", "tokens", "alerts", "gas")


def _screen_keyboard(cmd: str) -> list:
    """The bar under a command reply. Nothing for a command that acts."""
    from app import tgstyle
    if cmd not in _BUTTON_SCREENS:
        return []
    return tgstyle.nav("" if cmd in ("help", "start") else cmd)


def _fmt_dur(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _ago(ts) -> str:
    if not ts:
        return "—"
    d = int(time.time() - float(ts))
    if d < 60:
        return f"{d}s"
    if d < 3600:
        return f"{d // 60}m"
    if d < 86400:
        return f"{d // 3600}h"
    return f"{d // 86400}d"


def _col(name: str):
    from .. import db
    return db.get_collection(name)


class TelegramCommands:
    """Long-polling command handler. One instance, runs as a supervisor task."""

    def __init__(self) -> None:
        self._token = config.TELEGRAM_BOT_TOKEN if config.TELEGRAM_BOT_TOKEN_SET else ""
        # The only chat commands are answered in. Blank = answer anywhere.
        self._chat_id = str(config.COMMAND_CHAT_ID or "").strip()
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._boot_at = time.time()
        # chat_id -> ({admin user ids}, fetched_at). Only touched by /stop and
        # /restart, so it stays empty for a chat that never runs them.
        self._admin_cache: dict[int, tuple[set, float]] = {}

    def allowed(self, chat_id) -> bool:
        """Is this the chat we answer in?

        Chat ids are compared as strings so -1003946098130 from .env matches the
        int Telegram sends.
        """
        if not self._chat_id:
            return True
        return str(chat_id) == self._chat_id

    async def _is_group_admin(self, chat_id, user_id) -> bool:
        """True if Telegram itself lists user_id as an admin/creator of chat_id.

        Cached for _ADMIN_CACHE_TTL per chat. On a failed lookup, an existing
        cache entry is reused rather than treated as "nobody is admin" (breaks
        the command for real admins) or "everybody is admin" (the one outcome
        that must never happen for /stop). With no cache and no successful
        lookup, it fails closed — denied.
        """
        if user_id is None:
            return False
        now = time.time()
        cached = self._admin_cache.get(chat_id)
        if cached is not None and now - cached[1] < _ADMIN_CACHE_TTL:
            return user_id in cached[0]
        try:
            res = await self._api("getChatAdministrators", {"chat_id": chat_id})
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[CMD] getChatAdministrators failed: {exc}")
            return user_id in cached[0] if cached is not None else False
        if not res.get("ok"):
            log.warning(f"[CMD] getChatAdministrators rejected: {res.get('description')}")
            return user_id in cached[0] if cached is not None else False
        ids = {m["user"]["id"] for m in res.get("result", []) if m.get("user")}
        self._admin_cache[chat_id] = (ids, now)
        return user_id in ids

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN not set — get one from @BotFather and put it in .env"
            )
        self._session = aiohttp.ClientSession()
        # A leftover webhook would swallow updates and getUpdates would return
        # nothing at all, silently.
        try:
            await self._api("deleteWebhook", {"drop_pending_updates": "false"})
        except Exception as exc:  # noqa: BLE001
            # A webhook left in place swallows every update, so getUpdates would
            # return nothing at all and the bot would look simply dead.
            log.warning(f"[CMD] deleteWebhook failed: {exc}")
        await self.refresh_menu()

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("[CMD] Command handler stopped")

    async def refresh_menu(self) -> None:
        """Publish only the enabled commands to Telegram's "/" menu.

        Called at startup and again whenever a toggle changes, so switching a
        command off in the dashboard really removes it from the menu.

        When COMMAND_CHAT_ID is set the menu is published *scoped to that chat*
        and the default scope is emptied, so no other group or DM even sees a
        "/" list — matching the handler, which ignores them anyway.
        """
        try:
            enabled = await self._enabled_map()
            cmds = [
                {"command": name, "description": menu}
                for name, menu, _cat in COMMAND_SPEC
                if enabled.get(name, True)
            ]
            payload = json.dumps(cmds)

            if self._chat_id:
                scope = json.dumps({"type": "chat", "chat_id": self._chat_id})
                res = await self._api("setMyCommands",
                                      {"commands": payload, "scope": scope})
                if not res.get("ok"):
                    # Usually "chat not found" — the bot is not in that group yet.
                    log.warning(f"[CMD] menu for chat {self._chat_id} rejected: "
                                f"{res.get('description')}. Add the bot to that group.")
                # The RSI chat gets its own short menu: the settings screen and
                # the commands that answer there. Without this the "/" popup is
                # empty in that group and /menu looks like it does not exist,
                # even though it answers.
                if config.RSI_ALERT_CHAT_ID:
                    rsi_cmds = [c for c in cmds
                                if c["command"] == "menu"
                                or c["command"].startswith("rsi")]
                    res = await self._api("setMyCommands", {
                        "commands": json.dumps(rsi_cmds),
                        "scope": json.dumps({"type": "chat",
                                             "chat_id": config.RSI_ALERT_CHAT_ID})})
                    if not res.get("ok"):
                        log.warning(f"[CMD] menu for the RSI chat rejected: "
                                    f"{res.get('description')}")
                    else:
                        log.info(f"[CMD] RSI chat menu published — "
                                 f"{len(rsi_cmds)} command(s) in "
                                 f"{config.RSI_ALERT_CHAT_ID}")
                # And the Market Cap chat gets its own short menu, for the
                # same reason: that group only ever needs /menu and /mcap*.
                if config.MCAP_ALERT_CHAT_ID:
                    mcap_cmds = [c for c in cmds
                                 if c["command"] in ("menu", "mc")
                                 or c["command"].startswith("mcap")]
                    res = await self._api("setMyCommands", {
                        "commands": json.dumps(mcap_cmds),
                        "scope": json.dumps({"type": "chat",
                                             "chat_id": config.MCAP_ALERT_CHAT_ID})})
                    if not res.get("ok"):
                        log.warning(f"[CMD] menu for the Market Cap chat rejected: "
                                    f"{res.get('description')}")
                    else:
                        log.info(f"[CMD] Market Cap chat menu published — "
                                 f"{len(mcap_cmds)} command(s) in "
                                 f"{config.MCAP_ALERT_CHAT_ID}")
                # Empty everywhere else, so the "/" popup is blank in other chats.
                await self._api("setMyCommands",
                                {"commands": "[]",
                                 "scope": json.dumps({"type": "default"})})
                log.info(f"[CMD] Menu published — {len(cmds)} command(s) enabled, "
                         f"only in chat {self._chat_id}")
            else:
                await self._api("setMyCommands", {"commands": payload})
                log.info(f"[CMD] Menu published — {len(cmds)} command(s) enabled "
                         f"(no COMMAND_CHAT_ID set: answers in every chat)")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[CMD] setMyCommands failed: {exc}")

    async def run(self) -> None:
        where = f"answering only in chat {self._chat_id}" if self._chat_id \
                else "answering in every chat (COMMAND_CHAT_ID not set)"
        log.info(f"[CMD] Command handler started — long-polling getUpdates, {where}")
        while True:
            try:
                for update in await self._get_updates():
                    await self._observe(update)
                    if update.get("callback_query"):
                        await self._handle_button(update["callback_query"])
                    else:
                        await self._handle(update)
            except asyncio.CancelledError:
                return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                # Normal with long-polling: Telegram or the network drops an
                # idle connection. Nothing is lost — just poll again.
                await asyncio.sleep(1)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[CMD] poll error: {exc}")
                await asyncio.sleep(3)

    # ── Telegram I/O ──────────────────────────────────────────────────────────

    async def _api(self, method: str, params: dict, timeout: int = 15) -> dict:
        assert self._session is not None
        async with self._session.post(
            f"{TELEGRAM_API}/bot{self._token}/{method}",
            data=params, timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            return await resp.json()

    async def _get_updates(self) -> list:
        data = await self._api(
            "getUpdates",
            # my_chat_member fires the moment the bot is added to (or removed
            # from) a group — that is what makes a brand-new private group
            # discoverable in Settings → Find Chat ID.
            {"offset": self._offset, "timeout": 25,
             "allowed_updates": json.dumps(["message", "my_chat_member",
                                            "callback_query"])},
            timeout=35,
        )
        result = data.get("result", []) if isinstance(data, dict) else []
        if result:
            self._offset = result[-1]["update_id"] + 1
        return result

    async def _send(self, chat_id, text: str,
                    keyboard: Optional[list] = None) -> bool:
        try:
            params = {"chat_id": chat_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": "true"}
            if keyboard:
                # This API is form-encoded, so the markup goes as JSON text.
                params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
            r = await self._api("sendMessage", params)
            return bool(r.get("ok"))
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] send failed: {exc}")
            return False

    async def _edit(self, chat_id, message_id, text: str,
                    keyboard: Optional[list] = None) -> bool:
        """Redraw a screen in place. A Refresh button that posted a new copy
        would turn the chat into a stack of near-identical screens."""
        try:
            params = {"chat_id": chat_id, "message_id": message_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": "true"}
            if keyboard:
                params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
            r = await self._api("editMessageText", params)
            return bool(r.get("ok"))
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] edit failed: {exc}")
            return False

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _enabled_map(self) -> dict[str, bool]:
        try:
            docs = await _col("commands").find({}).to_list(100)
            return {d["command"].lstrip("/"): bool(d.get("enabled", True)) for d in docs}
        except Exception:
            return {}

    @staticmethod
    async def _observe(update: dict) -> None:
        """Remember every chat the bot sees, for the Settings chat-id lookup.

        Purely a note-to-self: nothing is subscribed to and nothing is
        forwarded — the chat is only written down so its numeric id can be
        copied into .env later.
        """
        member = update.get("my_chat_member") or {}
        cb_chat = ((update.get("callback_query") or {}).get("message") or {}).get("chat")
        chat = (update.get("message") or {}).get("chat") or member.get("chat") or cb_chat
        if not chat:
            return
        try:
            from .. import chatid
            await chatid.record_chat(chat, "bot added" if member else "message seen")
            if member:
                status = ((member.get("new_chat_member") or {}).get("status")) or "?"
                log.info(f"[CMD] chat noted: {chat.get('title') or chat.get('id')} "
                         f"({chat.get('id')}) — bot is now '{status}'")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] could not note chat {chat.get('id')}: {exc}")

    async def _handle_button(self, cb: dict) -> None:
        """A button press on an alert.

        Telegram keeps showing a spinner until answerCallbackQuery is sent, so
        that goes out whatever happens. Buttons are honoured from the alert
        chats too, not only the command chat — the whole point is acting on the
        alert where it arrived.
        """
        from .. import tgbuttons
        text, show_alert = "", False
        try:
            data = str(cb.get("data") or "")
            if data.startswith("cmd:"):
                # A command screen, redrawn in place. Handled here rather than
                # in tgbuttons because rendering one needs this handler's own
                # `_reply_for` — the same code path the typed command takes, so
                # a button and a command can never show different answers.
                text = await self._redraw(data[4:], cb)
            else:
                text, show_alert = await tgbuttons.handle_callback(cb)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[CMD] button {cb.get('data')!r} failed: {exc}")
            text = "Could not do that"
        try:
            await self._api("answerCallbackQuery", {
                "callback_query_id": cb.get("id"),
                "text": text[:200],
                "show_alert": "true" if show_alert else "false",
            })
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] answerCallbackQuery failed: {exc}")

    async def _redraw(self, cmd: str, cb: dict) -> str:
        """Re-render a screen onto the message its button was pressed on."""
        if cmd not in _BUTTON_SCREENS:
            return "Unknown screen"
        if not (await self._enabled_map()).get(cmd, True):
            return f"/{cmd} is switched off"
        message = cb.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (cb.get("from") or {}).get("id")
        body = await self._reply_for(cmd, chat_id, user_id, "")
        if not body:
            return ""
        await self._edit(chat_id, message.get("message_id"), body,
                         _screen_keyboard(cmd))
        # Empty toast: the screen itself changing is the feedback, and a popup
        # saying "refreshed" on every press is noise.
        return ""

    async def _handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        if not text.startswith("/"):
            # Not a command — but the RSI settings screen asks for an address
            # as an ordinary message after a chain is chosen, and that is the
            # only time a plain message means anything to this bot.
            from app import mcap_panel, rsi_panel
            if await rsi_panel.pending_address(chat_id, text):
                return
            await mcap_panel.pending_address(chat_id, text)
            return

        # A private chat is a customer's chat, and it gets a different bot from
        # the one the operator's group gets. Two command sets on one token:
        # /start connects an account, and a connected account may ask about
        # itself. What it may never do is touch the box — /stop, /restart,
        # /services and the rest turn scanners off for everybody, and a
        # customer pressing one would be turning the product off for the other
        # customers. Those are not refused here so much as never offered: the
        # menu published for a private chat does not contain them.
        if (msg.get("chat") or {}).get("type") == "private":
            await self._private_command(msg, text)
            return

        # Commands are answered in one group only, with one exception: the RSI
        # commands also answer in the RSI alert chat, because that is where its
        # alerts land and its settings screen belongs beside them.
        cmd_word = text.split()[0].lstrip("/").split("@")[0].lower()
        rsi_chat = ((cmd_word.startswith("rsi") or cmd_word == "menu")
                    and config.RSI_ALERT_CHAT_ID
                    and str(chat_id) == str(config.RSI_ALERT_CHAT_ID))
        # Same rule for the Market Cap chat: its own commands and /menu answer
        # where its alerts land.
        mcap_chat = ((cmd_word.startswith("mcap") or cmd_word in ("menu", "mc"))
                     and config.MCAP_ALERT_CHAT_ID
                     and str(chat_id) == str(config.MCAP_ALERT_CHAT_ID))
        if not rsi_chat and not mcap_chat and not self.allowed(chat_id):
            log.debug(f"[CMD] ignored '{text.split()[0]}' from chat {chat_id} "
                      f"(only {self._chat_id} is allowed)")
            return

        # "/status@MyBot arg" -> "status"
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        if cmd not in {c for c, _, _ in COMMAND_SPEC}:
            return                                    # unknown — stay silent

        enabled = await self._enabled_map()
        if not enabled.get(cmd, True):
            log.debug(f"[CMD] /{cmd} is disabled — ignored")
            return

        user_id = (msg.get("from") or {}).get("id")
        heartbeat.beat("command")
        started = time.perf_counter()
        try:
            reply = await self._reply_for(cmd, chat_id, user_id, text)
            ok = (await self._send(chat_id, reply, _screen_keyboard(cmd))
                  if reply else False)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[CMD] /{cmd} failed: {exc}")
            ok = False
        await self._record(cmd, ok, time.perf_counter() - started, user_id)

    # What a customer may ask in their own chat. Deliberately short: everything
    # here answers "what about my account", and nothing here changes the box.
    CUSTOMER_COMMANDS = ("start", "help", "plan", "myalerts", "ping")

    async def _private_command(self, msg: dict, text: str) -> None:
        """A command in somebody's own chat with the bot.

        Answered from their account rather than from the box: the chat id is
        what telegram_link bound to a username, so who is asking is already
        known and their plan decides the answer. An unconnected chat gets the
        one instruction that leads somewhere — how to connect — and nothing
        else, because there is nothing else it could be entitled to.
        """
        from app import notifier, telegram_link

        chat_id = (msg.get("chat") or {}).get("id")
        word = (text.split() or [""])[0].lstrip("/").split("@")[0].lower()

        # /start carries the connect token and is the one command that works
        # before there is an account behind the chat.
        if word == "start":
            await self._connect_account(msg, text)
            return

        username = await telegram_link.username_for(chat_id)
        if not username:
            await notifier.send_to(
                chat_id,
                "\U0001F44B This bot sends alerts for your dashboard account.\n\n"
                "Open <b>Profile \u2192 Connect Telegram</b> on the site and tap "
                "the link there — it connects this chat to your account.")
            return

        if word not in self.CUSTOMER_COMMANDS:
            await notifier.send_to(
                chat_id,
                "That one is not available here. In your own chat this bot "
                "answers <code>/plan</code>, <code>/myalerts</code>, "
                "<code>/ping</code> and <code>/help</code>.\n\n"
                "<i>Everything else lives on the dashboard.</i>")
            return

        await self._answer_customer(chat_id, username, word)

    async def _answer_customer(self, chat_id, username: str, word: str) -> None:
        """The four answers a customer's own chat can give."""
        from app import accounts, alert_subs, notifier

        if word == "ping":
            await notifier.send_to(chat_id, "\u2705 Here.")
            return

        doc = await accounts.by_username(username)
        if doc is None:
            await notifier.send_to(chat_id, "\u26A0\uFE0F That account no longer exists.")
            return
        plan = accounts.plan_of(doc)
        state = accounts.access(doc)

        if word == "help":
            await notifier.send_to(
                chat_id,
                f"\U0001F4AC <b>Your chat with SightLine</b>\n"
                f"Connected as <b>{username}</b>.\n\n"
                f"<code>/plan</code> — what you are on and how long is left\n"
                f"<code>/myalerts</code> — which feeds you have switched on\n"
                f"<code>/ping</code> — check the bot is awake\n\n"
                f"<i>Everything else is on the dashboard.</i>")
            return

        if word == "plan":
            left = f"{state.days_left} day{'s' if state.days_left != 1 else ''} left" \
                if state.days_left else state.reason or "not active"
            await notifier.send_to(
                chat_id,
                f"\U0001F4E6 <b>{plan.label}</b> — {state.status}\n"
                f"{left}\n\n"
                f"Telegram alerts: <b>{'yes' if plan.telegram_alerts else 'dashboard only'}</b>\n"
                f"Up to <b>{plan.alerts_per_day}</b> a day\n"
                f"RSI tokens <b>{plan.rsi_tokens}</b> · "
                f"Market Cap tokens <b>{plan.mcap_tokens}</b>")
            return

        if word == "myalerts":
            sub = await alert_subs.get(username)
            on = [alert_subs.FEEDS[k] for k, v in (sub.get("feeds") or {}).items()
                  if v and k in alert_subs.FEEDS]
            chains = ", ".join(alert_subs.CHAINS.get(c, c)
                               for c in (sub.get("chains") or [])) or "none"
            body = ("\n".join(f"\u2022 {f}" for f in on) if on
                    else "<i>Nothing switched on yet — Alert Rules on the site.</i>")
            await notifier.send_to(
                chat_id,
                f"\U0001F514 <b>Your feeds</b>\n{body}\n\n"
                f"Chains: {chains}\n"
                f"Mode: {sub.get('mode', 'instant')} · "
                f"cap {sub.get('daily_cap')} a day"
                + ("\n\n<i>Alerts are switched off in Alert Rules.</i>"
                   if not sub.get("enabled", True) else ""))
            return

    async def _connect_account(self, msg: dict, text: str) -> None:
        """`/start <token>` from somebody's own chat with the bot.

        Answers something either way: a person who taps a stale link and hears
        nothing assumes the product is broken.
        """
        from app import notifier, telegram_link
        chat_id = (msg.get("chat") or {}).get("id")
        parts = text.split()
        first = parts[0].lstrip("/").split("@")[0].lower() if parts else ""
        token = parts[1] if first == "start" and len(parts) > 1 else ""
        if not token:
            await notifier.send_to(
                chat_id,
                "👋 This bot sends alerts for your dashboard account.\n\n"
                "Open <b>Profile → Connect Telegram</b> on the site and tap the "
                "link there — it connects this chat to your account.")
            return
        try:
            doc = await telegram_link.finish(
                token, chat_id, (msg.get("from") or {}).get("username") or "")
        except ValueError as exc:
            await notifier.send_to(chat_id, f"⚠️ {exc}")
            return
        if doc is None:
            await notifier.send_to(
                chat_id, "⚠️ That link has expired. Ask for a new one on "
                         "<b>Profile → Connect Telegram</b> — they last fifteen "
                         "minutes.")
            return
        await notifier.send_to(
            chat_id,
            f"✅ Connected to <b>{doc.get('username')}</b>.\n"
            f"Your RSI and Market Cap alerts will arrive here.\n\n"
            f"<i>You can disconnect any time from your Profile.</i>")

    async def _record(self, cmd: str, ok: bool, seconds: float, user_id) -> None:
        """Real usage stats for the dashboard — no invented numbers."""
        try:
            await _col("commands").update_one(
                {"command": f"/{cmd}"},
                {"$inc": {"uses_total": 1, "errors_total": 0 if ok else 1},
                 "$set": {"last_used": time.time(),
                          "last_ms": round(seconds * 1000),
                          "last_user_id": user_id}},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] could not record use of /{cmd}: {exc}")

    async def _reply_for(self, cmd: str, chat_id, user_id, text: str = "") -> str:
        # The only commands that take arguments so far, and they take several.
        if cmd in ("menu", "mc") or cmd.startswith(("rsi", "mcap")):
            # /rsi and /mcap are settings screens — buttons, edited in place.
            # The rest stay as commands, which is what a phone keyboard is good
            # for when you already know the address you want to add.
            #
            # /menu opens whichever screen belongs to the chat it was sent in,
            # so each alert group has one way in and no wrong answers.
            if cmd == "menu":
                from app import mcap_panel, rsi_panel
                if (config.MCAP_ALERT_CHAT_ID
                        and str(chat_id) == str(config.MCAP_ALERT_CHAT_ID)):
                    await mcap_panel.open_panel(chat_id)
                else:
                    await rsi_panel.open_panel(chat_id)
                return ""
            if cmd == "rsi":
                from app import rsi_panel
                await rsi_panel.open_panel(chat_id)
                return ""
            if cmd == "mcap":
                from app import mcap_panel
                await mcap_panel.open_panel(chat_id)
                return ""
            if cmd == "mc":
                from app import mcap_panel, registry as reg
                if not (await reg.enabled_map()).get("mcap_checker", True):
                    return "🔎 Market Cap Check is switched off in Settings."
                await mcap_panel.open_check_panel(chat_id)
                return ""
            if cmd.startswith("mcap"):
                from app.scanners.mcap_commands import reply as mcap_reply
                return await mcap_reply(cmd, text)
            from app.scanners.rsi_commands import reply as rsi_reply
            return await rsi_reply(cmd, text)
        if cmd in ("start", "help"):
            return await self._msg_help()
        if cmd == "status":
            return await self._msg_status()
        if cmd == "services":
            return await self._msg_services()
        if cmd == "stats":
            return await self._msg_stats()
        if cmd == "watching":
            return await self._msg_watching()
        if cmd == "tokens":
            return await self._msg_tokens()
        if cmd == "alerts":
            return await self._msg_alerts()
        if cmd == "gas":
            return await self._msg_gas()
        if cmd == "ping":
            return "🟢 <b>pong</b> — bot is alive"
        if cmd in ADMIN_ONLY_COMMANDS and not await self._is_group_admin(chat_id, user_id):
            return "🔒 Only a group admin can use this command."
        if cmd == "stop":
            return await self._msg_stop(user_id)
        if cmd == "restart":
            return await self._msg_restart(user_id)
        return ""

    # ── Replies (all read from MongoDB) ───────────────────────────────────────

    async def _msg_help(self) -> str:
        enabled = await self._enabled_map()
        from app import tgstyle
        # Grouped by the category each command declares, because a flat list of
        # thirty is a wall — and the buttons below reach the six anybody
        # actually presses, so this reads as reference rather than the way in.
        by_cat: dict[str, list[str]] = {}
        for name, menu, cat in COMMAND_SPEC:
            if enabled.get(name, True) and name != "start":
                by_cat.setdefault(cat, []).append(f"/{name} — {esc(menu)}")
        lines = ["Cross-chain moves, Robinhood launches, premium calls and "
                 "high-gas early buys — as they happen.", tgstyle.SPACER]
        for cat, cmds in by_cat.items():
            lines.append(f"<b>{esc(cat)}</b>")
            lines.extend(cmds)
            lines.append(tgstyle.SPACER)
        return tgstyle.screen("SightLine", "🤖", lines,
                              note="Tap a button below, or send any command.")

    async def _msg_status(self) -> str:
        from .. import db, supervisor
        workers = supervisor.diagnostics().get("workers", {})
        alive = [k for k, v in workers.items() if v]
        from app import tgstyle
        return tgstyle.screen("Status", "📊", [
            "🟢 <b>Running</b>",
            f"⏱ up {_fmt_dur(supervisor.uptime_seconds())}",
            f"⚙️ {len(alive)} of {len(workers)} workers alive",
            f"🗄 database · {db.backend_name()}",
        ])

    async def _msg_services(self) -> str:
        from .. import registry, supervisor
        svcs = await registry.list_services()
        states = supervisor.service_states({s["id"]: bool(s["enabled"]) for s in svcs})
        icon = {"running": "🟢", "stopped": "🔴", "disabled": "⚪"}
        out = {"bot": [], "chain": [], "rpc": []}
        for s in svcs:
            st = states.get(s["id"], {}).get("status", "unknown")
            out.setdefault(s["category"], []).append(
                f"{icon.get(st, '❔')} {esc(s['label'])} — {st}"
            )
        from app import tgstyle
        lines: list[str] = []
        for cat, title in (("bot", "Bots"), ("chain", "Chains"), ("rpc", "RPCs")):
            if out.get(cat):
                lines.append(f"<b>{title}</b>")
                lines.extend(out[cat])
                lines.append(tgstyle.SPACER)
        return tgstyle.screen("Services", "🔀", lines)

    async def _msg_stats(self) -> str:
        day = time.time() - 86400
        tokens, alerts = _col("tokens"), _col("alerts")
        watch = await self._watchlist()
        from app import tgstyle
        return tgstyle.screen("Stats", "📈", [
            f"👀 watching now · <b>{len(watch)}</b>",
            f"🪙 tokens · <b>{await tokens.count_documents({})}</b>"
            f" · {await tokens.count_documents({'created_at': {'$gte': day}})} in 24h",
            f"🔔 alerts · <b>{await alerts.count_documents({})}</b>"
            f" · {await alerts.count_documents({'created_at': {'$gte': day}})} in 24h",
            f"⚡ cross-chain · "
            f"<b>{await alerts.count_documents({'type': 'Cross-Chain Match'})}</b>",
            f"⛽ high-gas buys · "
            f"<b>{await _col('gas_alerts').count_documents({})}</b>",
            f"🎯 premium calls · "
            f"<b>{await _col('premium_detections').count_documents({})}</b>",
        ])

    @staticmethod
    async def _watchlist() -> list:
        from .. import watchlist
        return await watchlist.active()

    async def _msg_watching(self) -> str:
        from app import tgstyle
        wl = await self._watchlist()
        if not wl:
            return tgstyle.screen("Watching now", "👀",
                                  ["Nothing is being watched right now."])
        now = time.time()
        lines = []
        for d in wl[:25]:
            rem = max(0, int((d.get("expires_at", 0) - now) / 60))
            lines.append(f"• <b>${esc(d.get('symbol'))}</b> · "
                         f"{tgstyle.usd(float(d.get('mcap_usd') or 0))} · "
                         f"{rem}m left")
        note = f"and {len(wl) - 25} more" if len(wl) > 25 else ""
        return tgstyle.screen(f"Watching now · {len(wl)}", "👀",
                              lines, note=note)

    async def _msg_tokens(self) -> str:
        from app import tgstyle
        docs = await _col("tokens").find({}).to_list(500)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return tgstyle.screen("Recent tokens", "🪙",
                                  ["Nothing detected yet."])
        return tgstyle.screen("Recent tokens", "🪙", [
            f"• <b>${esc(t.get('symbol'))}</b> · "
            f"{tgstyle.chain_label(t.get('chain') or '')} · "
            f"{_ago(t.get('created_at'))} ago"
            for t in docs[:10]
        ])

    async def _msg_alerts(self) -> str:
        from app import tgstyle
        docs = await _col("alerts").find({}).to_list(500)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return tgstyle.screen("Recent alerts", "🔔", ["Nothing yet."])
        return tgstyle.screen("Recent alerts", "🔔", [
            f"• <b>{esc(a.get('token_symbol') or a.get('type'))}</b> · "
            f"{tgstyle.chain_label(a.get('chain') or '')} · "
            f"{_ago(a.get('created_at'))} ago"
            for a in docs[:8]
        ])

    async def _msg_gas(self) -> str:
        from app import tgstyle
        docs = await _col("gas_alerts").find({}).to_list(200)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return tgstyle.screen(
                "High-gas early buys", "⛽", ["None caught yet."],
                note=f"Fires above {config.MIN_FEE_ETH} ETH of gas on one buy.")
        return tgstyle.screen("High-gas early buys", "⛽", [
            f"• <b>${esc(g.get('symbol'))}</b> · "
            f"{float(g.get('fee_eth') or 0):.6f} ETH · "
            f"age {g.get('age_seconds', '?')}s · {_ago(g.get('created_at'))} ago"
            for g in docs[:8]
        ], note=f"Fires above {config.MIN_FEE_ETH} ETH of gas on one buy.")

    # ── Bot control (admin-only, the only two commands that change state) ──────
    #
    # /stop turns off every registry toggle except bot_commands, and snapshots
    # exactly which ones it touched so /restart can undo precisely that — not
    # "everything", which would also re-enable anything the user had already
    # switched off on purpose before /stop ran. bot_commands itself is never
    # touched: turning it off would kill this very handler, and /restart could
    # then never be heard.

    async def _msg_stop(self, user_id) -> str:
        from .. import registry
        svcs = await registry.list_services()
        to_stop = [s for s in svcs if s.get("enabled") and s["id"] != "bot_commands"]
        if not to_stop:
            return "⚪ Already stopped — nothing else was on."
        await _col("bot_control").update_one(
            {"_id": "stop_snapshot"},
            {"$set": {"ids": [s["id"] for s in to_stop],
                     "stopped_at": time.time(), "stopped_by": user_id}},
            upsert=True,
        )
        for s in to_stop:
            await registry.set_enabled(s["id"], False)
        names = ", ".join(esc(s["label"]) for s in to_stop)
        return (
            "🔴 <b>Bot stopped</b>\n\n"
            f"Turned off {len(to_stop)}: {names}\n\n"
            "Dashboard and this chat stay reachable — /restart brings back "
            "exactly this, or use Settings."
        )

    async def _msg_restart(self, user_id) -> str:
        from .. import registry
        snap = await _col("bot_control").find_one({"_id": "stop_snapshot"})
        ids = (snap or {}).get("ids") or []
        if not ids:
            return "🟢 Nothing to restart — the bot was not stopped with /stop."
        by_id = {s["id"]: s for s in await registry.list_services()}
        restored = []
        for sid in ids:
            await registry.set_enabled(sid, True)
            if sid in by_id:
                restored.append(by_id[sid]["label"])
        await _col("bot_control").delete_one({"_id": "stop_snapshot"})
        names = ", ".join(esc(n) for n in restored)
        return f"🟢 <b>Bot restarted</b>\n\nTurned back on {len(restored)}: {names}"
