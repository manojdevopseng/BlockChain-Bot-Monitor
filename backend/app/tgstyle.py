"""One house style for everything this bot says on Telegram.

Six features grew their own message shapes independently. The gas alert wrote
"Token Name:\\n<b>x</b>\\n\\n" over nine paragraphs and left a bare URL at the
bottom; the cross-chain alert packed three facts per line with inline links;
the launchpad alert had buttons and the X monitor did not. Read one after
another in the same chat they look like five different products.

So every alert is now assembled here, out of the same parts:

    🚀 PONS V2 LAUNCH · 12s ago         ← what happened, and how long ago
    ━━━━━━━━━━━━━━━━━━━━                 ← the rule, everywhere, once
    $FWB — Fake World Brokers            ← the thing itself
    👤 @fwbdotcash ✅ · 83 followers      ← the facts, one per line, emoji key
    🟢 Strong Signal · dev bought 0.301 Ξ
    ❝ bio, when there is one ❞
    0x2a9e…                              ← the address alone, tappable
                                          ← buttons below, never links in body

Two rules worth keeping when adding a sixth:

  the address sits alone on its own line in <code>, because a tap copies the
  whole line and that is the one thing anybody does with an alert;

  links are buttons, never text. A URL in the body is a line of noise that
  Telegram cannot make tappable prettily, and it pushes the address down.
"""

from __future__ import annotations

import html
import time
from typing import Iterable, Optional

RULE = "━━━━━━━━━━━━━━━━━━━━"

# What each chain is called in the places a button has to point at.
_GMGN = {"eth": "eth", "ethereum": "eth", "rbh": "robinhood",
         "robinhood": "robinhood", "sol": "sol", "solana": "sol",
         "bsc": "bsc", "bnb": "bsc"}
_DEX = {"eth": "ethereum", "rbh": "robinhood", "sol": "solana",
        "bsc": "bsc", "bnb": "bsc"}
_CHAIN_LABEL = {"eth": "Ethereum", "rbh": "Robinhood", "sol": "Solana",
                "bsc": "BNB Chain", "bnb": "BNB Chain"}


def esc(value) -> str:
    return html.escape(str(value or ""))


def chain_label(chain: str) -> str:
    return _CHAIN_LABEL.get((chain or "").lower(), (chain or "").upper())


def ago(when: Optional[float]) -> str:
    """"12s ago" / "4m ago". Blank when there is no timestamp — an alert that
    cannot say how old it is should say nothing rather than "0s"."""
    if not when:
        return ""
    gap = max(0, int(time.time() - float(when)))
    if gap < 60:
        return f"{gap}s ago"
    if gap < 3600:
        return f"{gap // 60}m ago"
    if gap < 86400:
        return f"{gap // 3600}h ago"
    return f"{gap // 86400}d ago"


def usd(value: float) -> str:
    value = float(value or 0)
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def card(*, icon: str, kind: str, chain: str = "", when: Optional[float] = None,
         symbol: str = "", name: str = "", lines: Iterable[str] = (),
         quote: str = "", address: str = "", banners: Iterable[str] = ()) -> str:
    """One alert, assembled.

    `banners` go above the heading — they are the reason to stop scrolling
    (a keyword matched, a watched account, a strong dev buy). `lines` are the
    facts, already formatted, and empty ones are dropped so a caller can write
    them conditionally without minding the joins.
    """
    head = f"{icon} <b>{esc(kind)}</b>"
    if chain:
        head += f" · {esc(chain_label(chain))}"
    stamp = ago(when)
    if stamp:
        head += f" · {stamp}"

    title = ""
    if symbol:
        title = f"<b>${esc(symbol)}</b>"
        if name and name.strip().lower() != symbol.strip().lower():
            title += f" — {esc(name)}"

    out = [b for b in banners if b]
    out.append(head)
    out.append(RULE)
    if title:
        out.append(title)
    out.extend([ln for ln in lines if ln])
    if quote:
        out.append(f"\n<blockquote>{esc(quote[:300])}</blockquote>")
    if address:
        # Alone, last, and in code: the tap target.
        out.append(f"\n<code>{esc(address)}</code>")
    return "\n".join(out)


def keyboard(*, chain: str = "", address: str = "", x_link: str = "",
             website: str = "", group: str = "", mute: bool = True,
             extra: Optional[list[list[dict]]] = None) -> list[list[dict]]:
    """The buttons under an alert, in the order they get pressed.

    Charts first, because that is the reflex; trading second and only where a
    bot actually serves that chain — a Maestro button on Robinhood Chain is a
    dead end dressed as an action; the account's own links third; and the
    quiet-it-down actions last, where they cannot be hit by accident.
    """
    slug = _GMGN.get((chain or "").lower(), "")
    rows: list[list[dict]] = []

    look: list[dict] = []
    if slug and address:
        look.append({"text": "📊 GMGN", "url": f"https://gmgn.ai/{slug}/token/{address}"})
        dex = _DEX.get((chain or "").lower())
        if dex:
            look.append({"text": "📈 Chart",
                         "url": f"https://dexscreener.com/{dex}/{address}"})
    if look:
        rows.append(look)

    trade: list[dict] = []
    if address and slug == "eth":
        trade = [{"text": "🤖 Maestro", "url": f"https://t.me/maestro?start={address}"},
                 {"text": "🍌 Banana",
                  "url": f"https://t.me/BananaGunSniper_bot?start=snp_{address}"}]
    elif address and slug == "sol":
        trade = [{"text": "🐂 BullX", "url": f"https://neo.bullx.io/terminal?chainId=1399811149&address={address}"},
                 {"text": "🤖 Trojan", "url": f"https://t.me/solana_trojanbot?start=r-{address}"}]
    if trade:
        rows.append(trade)

    social = [b for b in (
        {"text": "𝕏 Profile", "url": x_link} if x_link else None,
        {"text": "🌐 Website", "url": website} if website else None,
    ) if b]
    if social:
        rows.append(social)

    if extra:
        rows.extend([r for r in extra if r])

    if mute and address:
        quiet = [{"text": "🔇 Mute 24h", "callback_data": f"mt:{address[:56]}"}]
        if group:
            quiet.append({"text": "🔕 Mute group", "callback_data": f"mg:{group[:56]}"})
        rows.append(quiet)
    return rows


# ── the command screens ──────────────────────────────────────────────────────

def screen(title: str, icon: str, lines: Iterable[str],
           note: str = "") -> str:
    """A reply to a command. Same rule and the same shape as an alert, so the
    chat reads as one thing rather than as a bot and a separate help system."""
    out = [f"{icon} <b>{esc(title)}</b>", RULE]
    out.extend([ln for ln in lines if ln])
    if note:
        out.append(f"\n<i>{esc(note)}</i>")
    return "\n".join(out)


def row(*buttons: tuple[str, str]) -> list[dict]:
    """A row of callback buttons: row(("🔄 Refresh", "cmd:status"), …)."""
    return [{"text": text, "callback_data": data} for text, data in buttons]


# The bar that sits under every command screen, so any screen reaches any
# other in one tap and nobody has to remember a command name.
def nav(current: str = "") -> list[list[dict]]:
    picks = [("📊 Status", "status"), ("🔀 Services", "services"),
             ("📈 Stats", "stats"), ("🪙 Tokens", "tokens"),
             ("🔔 Alerts", "alerts"), ("⛽ Gas", "gas")]
    live = [(label, f"cmd:{name}") for label, name in picks if name != current]
    rows = [row(*live[i:i + 3]) for i in range(0, len(live), 3)]
    if current:
        rows.insert(0, row((f"🔄 Refresh", f"cmd:{current}")))
    return rows
