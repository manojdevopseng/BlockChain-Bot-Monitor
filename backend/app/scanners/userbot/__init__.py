"""The Telethon userbot — premium-group mirroring, signal forwarding and
premium-caller address capture.

Was one 946-line `scanners/forwarder.py` holding a single class that did
Telethon plumbing, per-chat rate limiting, three chains' worth of on-chain
verification and five independent Telegram handlers. Split by concern:

    common.py    constants, regexes, the shared logger
    store.py     Mongo reads for premium groups / keywords / Otto rules
    sending.py   per-chat pacing + FloodWait handling
    onchain.py   RPC reads over rotating endpoint pools
    premium.py   the ETH / RBH / SOL panel capture
    handlers.py  the five Telegram event handlers
    client.py    TelegramForwarder itself: lifecycle, watchers, shared state

Also renamed out of `scanners/forwarder.py` because `routers/forwarder.py`
exists too, and having both meant opening the wrong file regularly.
"""

from .common import (GATE_BUYBOT, GATE_CALL, GATE_DEXS, GATE_OTTO, GATE_PREMIUM,
                     GATE_PREMIUM_ETH, GATE_PREMIUM_RBH, GATE_PREMIUM_SOL)
from .client import TelegramForwarder

__all__ = [
    "TelegramForwarder",
    "GATE_BUYBOT", "GATE_CALL", "GATE_DEXS", "GATE_OTTO", "GATE_PREMIUM",
    "GATE_PREMIUM_ETH", "GATE_PREMIUM_RBH", "GATE_PREMIUM_SOL",
]
