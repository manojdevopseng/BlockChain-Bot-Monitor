#!/usr/bin/env python3
"""One-time Telethon (userbot) login — run this in a terminal, once per server.

Why a separate script?
    The backend runs under uvicorn/systemd, which has no interactive stdin. If
    Telethon asked for a phone number there it would raise EOFError and take the
    service down. So the login happens here, interactively, and only the
    resulting .session file is used by the running bot.

Usage (from the backend/ folder, with the venv active):

    python scripts/telethon_login.py

It reads TELETHON_API_ID / TELETHON_API_HASH / TELETHON_SESSION from .env,
asks for your phone number and the code Telegram sends you (plus your 2FA
password if you have one), and writes  <TELETHON_SESSION>.session  next to the
backend folder. Restart the backend afterwards and the Forwarder will start.

You can also log in on your laptop and copy the .session file to the server —
the file is portable, but it must be used by only ONE process at a time.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

try:
    from telethon import TelegramClient
    from telethon.errors import (
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
    )
except ImportError:
    sys.exit("telethon is not installed — run:  pip install -r requirements.txt")

from app.config import settings


def _fail(msg: str) -> None:
    print(f"\n  ERROR: {msg}\n")
    raise SystemExit(1)


async def main() -> None:
    api_id = settings.telethon_api_id
    api_hash = settings.telethon_api_hash
    session = settings.telethon_session or "final_session"
    session_path = BACKEND / f"{session}.session"

    print("=" * 66)
    print("  Telethon userbot login")
    print("=" * 66)
    print(f"  session file : {session_path}")
    print(f"  api_id       : {api_id or '(not set)'}")
    print()

    if not api_id or not api_hash:
        _fail(
            "TELETHON_API_ID / TELETHON_API_HASH missing in .env\n"
            "         Get them from https://my.telegram.org → API development tools"
        )

    # The session is created in the backend folder so the running app finds it.
    client = TelegramClient(str(BACKEND / session), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"  Already logged in as: {me.first_name} (@{me.username or 'no-username'})")
        print("  Nothing to do — the session file is valid.\n")
        await client.disconnect()
        return

    phone = input("  Phone number (with country code, e.g. +919876543210): ").strip()
    if not phone:
        await client.disconnect()
        _fail("no phone number entered")

    try:
        await client.send_code_request(phone)
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        _fail(f"could not send the login code: {exc}")

    print("  Telegram has sent you a login code (check the Telegram app).")
    code = input("  Login code: ").strip()

    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        # Two-step verification is enabled on the account.
        import getpass
        password = getpass.getpass("  2FA password: ")
        try:
            await client.sign_in(password=password)
        except Exception as exc:  # noqa: BLE001
            await client.disconnect()
            _fail(f"2FA sign-in failed: {exc}")
    except PhoneCodeInvalidError:
        await client.disconnect()
        _fail("that login code was not valid — run the script again")
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        _fail(f"sign-in failed: {exc}")

    me = await client.get_me()
    await client.disconnect()

    print()
    print("  Logged in as:", f"{me.first_name} (@{me.username or 'no-username'})")
    print("  Session saved:", session_path)
    print()
    print("  Next: restart the backend — the Forwarder will start automatically.")
    print("    sudo systemctl restart blockchain-bot-api      # on EC2")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  cancelled")
