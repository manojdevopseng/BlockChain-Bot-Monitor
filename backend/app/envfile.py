"""Safe read/update of backend/.env from the dashboard.

Only the keys listed in `EDITABLE` may be changed, and the file is rewritten
atomically (temp file + os.replace) preserving every other line, comment and
ordering. A missing key is appended rather than silently dropped.

Used by the Settings and RPC Monitor pages so values that need tuning — the
GMGN web fingerprint that expires, a rate-limited RPC endpoint, the gas-fee
threshold, which sources the Robinhood detector listens to — can be changed
without SSH-ing into the EC2 box.

Every field declares its type. That matters: .env values are strings, but the
scanners compare them as numbers (`fee_eth >= config.MIN_FEE_ETH`), so writing
a raw string into the live config would raise a TypeError on the next swap.
`_coerce` validates and converts before anything is stored or applied.
"""

from __future__ import annotations

import os
import re

from .config import ROOT, settings
from .scanners.slog import get_logger

log = get_logger(__name__)

ENV_PATH = ROOT / ".env"

# Fields the dashboard may write.
#   kind    — text | number | int | bool  (drives validation AND the UI control)
#   secret  — mask the current value in the API response
#   group   — heading it appears under in Settings
#   applies — how the change takes effect, shown to the user:
#               "live"        next time the value is read, nothing to restart
#               "worker:<id>" that scanner is restarted for it (done for you)
EDITABLE: dict[str, dict] = {
    "GMGN_CLIENT_ID": {
        "label": "GMGN Client ID", "kind": "text", "group": "GMGN Credentials",
        "applies": "live",
    },
    "GMGN_DEVICE_ID": {
        "label": "GMGN Device ID", "kind": "text", "group": "GMGN Credentials",
        "applies": "live",
    },
    "GMGN_FP_DID": {
        "label": "GMGN Fingerprint (fp_did)", "kind": "text", "group": "GMGN Credentials",
        "applies": "live",
    },
    "CF_CLEARANCE": {
        "label": "Cloudflare clearance cookie", "kind": "text", "secret": True,
        "group": "GMGN Credentials", "applies": "live",
    },
    "GMGN_API_KEY": {
        "label": "GMGN API key", "kind": "text", "secret": True,
        "group": "GMGN Credentials", "applies": "live",
    },
    "MIN_FEE_ETH": {
        "label": "Min gas fee to alert (ETH)", "kind": "number", "group": "Detection Tuning",
        "min": 0.0, "applies": "live",
        "help": "A single early buy paying this much gas fires the ETH Gas Fees "
                "alert. Lower = more alerts. Reference bot uses 0.0009.",
    },
    "SOL_WATCH_WINDOW": {
        "label": "SOL watch window (minutes)", "kind": "int", "group": "Detection Tuning",
        "min": 1, "applies": "live",
        "help": "How long a triggered SOL ticker stays eligible for a cross-chain "
                "match. Applies to newly triggered tokens; ones already being "
                "watched keep the window they started with.",
    },
    # ── RPC endpoints ────────────────────────────────────────────────────────
    # The sockets every on-chain scanner rides. Editable here because the fix
    # for a rate-limited or dead provider is a new URL, and that fix should not
    # need an SSH session — the day it is needed is the day the scanners are
    # already blind. Each carries its provider's API key, so they are masked.
    #
    # The fallback slots are the reason this group exists: with one endpoint per
    # chain, a provider exhausting its quota takes that chain down completely.
    # Measured on 2026-07-29: Alchemy's quota went and ETH and Robinhood both
    # stopped inside a minute, and Helius took SOL discovery — and with it the
    # market cap watch — for eight hours. All three fallbacks were empty.
    "ETH_RPC_WSS": {
        "label": "ETH WebSocket #1", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Ethereum", "applies": "worker:eth",
        "help": "Tried first. On a quota rejection rotation goes 1 → 2 → 3 → 1.",
    },
    "ETH_RPC_WSS_FALLBACK": {
        "label": "ETH WebSocket #2", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Ethereum", "applies": "worker:eth",
        "help": "Use a different provider — a second URL on the same account "
                "shares the same quota and dies with it.",
    },
    "ETH_RPC_WSS_FALLBACK2": {
        "label": "ETH WebSocket #3", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Ethereum", "applies": "worker:eth",
        "help": "Third endpoint. With all three refusing you get one alert and "
                "rotation keeps going, so it recovers when a quota resets.",
    },
    # A different job again: eth_getCode + token-metadata reads for the
    # premium-caller detection (Premium ETH toggle in Settings → Bots), nothing
    # to do with on-chain discovery above. Found live on 2026-07-30 that this
    # key had hit Alchemy's *monthly* cap — a rotation-proof failure mode a
    # per-second-429 fallback doesn't help with; only a genuinely different
    # key/plan does.
    "ETH_RPC_HTTP": {
        "label": "ETH HTTP #1 — premium-caller detection (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Ethereum", "applies": "live",
        "help": "Used by Settings → Bots → Premium ETH to confirm an address "
                "seen in a premium group is a real contract before recording "
                "it. Applied on the next check, no restart.",
    },
    "ETH_RPC_HTTP_FALLBACK": {
        "label": "ETH HTTP #2 — premium-caller detection (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Ethereum", "applies": "live",
        "help": "Used when #1 keeps failing, then back to #1 if #2 fails too.",
    },
    # Its own card, not part of the Ethereum one: ETH Gas Fees is a separate
    # feature with its own switch in Settings → Bots, and it runs on its own
    # provider key precisely so its load stays away from new-pair detection.
    # Sharing a card with the discovery endpoints implied they were one thing.
    "GAS_RPC_WSS": {
        "label": "Gas Fees WebSocket", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — ETH Gas Fees", "applies": "worker:eth",
        "help": "Watches every detected pair for a high-gas early buy. It holds "
                "one subscription per watched pair, so on a shared key it can "
                "eat the compute units new-pair detection needs. Blank = share "
                "ETH WebSocket #1.",
    },
    "GAS_RPC_WSS_FALLBACK": {
        "label": "Gas Fees WebSocket #2", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — ETH Gas Fees", "applies": "worker:eth",
        "help": "A second provider for the gas socket. It had none, so when its "
                "key was rate-limited it retried the one refused URL every 60 "
                "seconds — use a different provider, not another URL on the "
                "same account.",
    },
    "GAS_RPC_HTTP": {
        "label": "Gas Fees HTTP", "kind": "http", "secret": True,
        "group": "RPC Endpoints — ETH Gas Fees", "applies": "live",
        "help": "Reads the gas paid on each buy's transaction receipt. Blank = "
                "share ETH HTTP #1.",
    },
    "RBH_RPC_WSS": {
        "label": "Robinhood WebSocket #1", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood Chain", "applies": "worker:rbh",
        "help": "Tried first. On a quota rejection rotation goes 1 → 2 → 3 → 1.",
    },
    "RBH_RPC_WSS_FALLBACK": {
        "label": "Robinhood WebSocket #2", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood Chain", "applies": "worker:rbh",
        "help": "Use a different provider from #1.",
    },
    "RBH_RPC_WSS_FALLBACK2": {
        "label": "Robinhood WebSocket #3", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood Chain", "applies": "worker:rbh",
        "help": "Third endpoint.",
    },
    "RBHX_RPC_WSS": {
        "label": "RBH-X Monitor WebSocket #1", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood X Monitor", "applies": "worker:rbhx",
        "help": "Tried first. On a quota rejection rotation goes 1 → 2 → 3 → 1. "
                "Leave all three blank and the monitor borrows the Robinhood "
                "Chain endpoints instead.",
    },
    "RBHX_RPC_WSS_FALLBACK": {
        "label": "RBH-X Monitor WebSocket #2", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood X Monitor", "applies": "worker:rbhx",
        "help": "Use a different provider from #1.",
    },
    "RBHX_RPC_WSS_FALLBACK2": {
        "label": "RBH-X Monitor WebSocket #3", "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Robinhood X Monitor", "applies": "worker:rbhx",
        "help": "Third endpoint.",
    },
    # Same split as ETH above: premium-caller detection (Premium RBH toggle),
    # unrelated to the on-chain discovery WebSockets.
    "RBH_RPC_HTTP": {
        "label": "RBH HTTP #1 — premium-caller detection (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Robinhood Chain", "applies": "live",
        "help": "Used by Settings → Bots → Premium RBH to confirm an address "
                "seen in a premium group is a real contract before recording "
                "it. Applied on the next check, no restart.",
    },
    "RBH_RPC_HTTP_FALLBACK": {
        "label": "RBH HTTP #2 — premium-caller detection (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Robinhood Chain", "applies": "live",
        "help": "Used when #1 keeps failing, then back to #1 if #2 fails too.",
    },
    # BNB Chain has no discovery scanner — only the premium-caller check — so
    # it is an HTTP pair and nothing else. Same 2-way rotation as the others:
    # #1 refuses, #2 is tried; #2 refuses, back to #1.
    "BNB_RPC_HTTP": {
        "label": "BNB HTTP #1 — premium-caller detection",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — BNB Chain", "applies": "live",
        "help": "Used by Settings → Bots → Premium BNB to confirm an address "
                "seen in a premium group is a real contract on BSC before "
                "recording it. Blank = BNB detection is off entirely. Applied "
                "on the next check, no restart.",
    },
    "BNB_RPC_HTTP_FALLBACK": {
        "label": "BNB HTTP #2 — premium-caller detection",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — BNB Chain", "applies": "live",
        "help": "Used when #1 keeps failing, then back to #1 if #2 fails too. "
                "Use a different provider — a second URL on the same account "
                "shares its quota and dies with it.",
    },
    # SOL runs two unrelated jobs on two unrelated endpoints, unlike ETH/RBH
    # where one WSS URL covers everything — spelled out in every label below so
    # it stops looking like one RPC that is "just not configured properly".
    "SOL_RPC_WSS": {
        "label": "SOL WebSocket #1 — discovery + market cap (Helius)",
        "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Solana", "applies": "live",
        "help": "Tried first. Carries on-chain launch discovery AND the trade "
                "stream the market cap watch reads, so losing both endpoints "
                "also stops anything reaching the Telegram filter. Needs a "
                "provider that supports logsSubscribe — Alchemy's Solana "
                "endpoint does not. Applied on the next reconnect, no restart.",
    },
    "SOL_RPC_WSS_FALLBACK": {
        "label": "SOL WebSocket #2 — discovery + market cap (Helius)",
        "kind": "wss", "secret": True,
        "group": "RPC Endpoints — Solana", "applies": "live",
        "help": "Used when #1 keeps failing, then back to #1 if #2 fails too. "
                "Must also support logsSubscribe, so a second Helius key or "
                "QuickNode/Triton — not Alchemy.",
    },
    "SOL_RPC_HTTP": {
        "label": "SOL HTTP #1 — premium-group check (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Solana", "applies": "live",
        "help": "Unrelated to the two above: this is a single getAccountInfo "
                "call the forwarder makes to confirm a Solana address seen in a "
                "premium Telegram group is a real on-chain account before "
                "recording it. Off entirely if blank — that detection feature "
                "just doesn't run. Applied on the next check, no restart.",
    },
    "SOL_RPC_HTTP_FALLBACK": {
        "label": "SOL HTTP #2 — premium-group check (Alchemy)",
        "kind": "http", "secret": True,
        "group": "RPC Endpoints — Solana", "applies": "live",
        "help": "Used when #1 keeps failing, then back to #1 if #2 fails too.",
    },

    "AI_SCAN_INTERVAL": {
        "label": "AI judging interval (seconds)", "kind": "int", "group": "AI",
        "min": 1, "applies": "live",
        "help": "How often the agent looks for launches to judge. A launch "
                "waits up to this long before it is asked about, so it is most "
                "of the gap between when a token appears in X Links and when it "
                "appears in Decisions. Measured at 20s: median 22.6s from "
                "launch to verdict. Lower does not cost more model calls — the "
                "same tokens are judged, just sooner — but the loop queries the "
                "database each pass.",
    },
    "AI_TELEGRAM_MCAP_USD": {
        "label": "Telegram market cap bar ($)", "kind": "number", "group": "AI",
        "min": 0.0, "applies": "live",
        "help": "A launch from a link's burst of five that reaches this inside "
                "its first minute is flagged Telegram and sent to the chat. "
                "Raise it to send fewer.",
    },
    "AI_MIN_CONFIDENCE": {
        "label": "Minimum narrative confidence", "kind": "int", "group": "AI",
        "min": 1, "applies": "live",
        "help": "The model scores its match 1-10. Below this the verdict is "
                "rejected rather than matched.",
    },
    "AI_DRY_RUN": {
        "label": "Dry run (record, send nothing)", "kind": "bool", "group": "AI",
        "applies": "live",
        "help": "On, decisions are recorded and the Telegram flag is set but no "
                "message is sent. Turn it off when the filters look right.",
    },
    # Which sources the Robinhood detector subscribes to. Each is its own WS
    # subscription, so a token launched through a source that is off is never
    # seen at all and a matching SOL ticker cannot fire. All four are here
    # because they are one decision made four ways — only V3 used to be
    # editable, filed under "Detection Tuning" next to unrelated fee and window
    # numbers, so the other three could only be changed by editing .env.
    # The SOL to RBH switch in Settings → Bots is the master over all of them.
    "RBH_NOXA_ENABLED": {
        "label": "noxa.fun launches", "kind": "bool",
        "group": "Robinhood Detection Sources", "applies": "worker:rbh",
        "help": "The launchpad most Robinhood tokens come from (TokenCreated "
                "event). Usually the one that matters.",
    },
    "RBH_V2_ENABLED": {
        "label": "Uniswap V2 pairs", "kind": "bool",
        "group": "Robinhood Detection Sources", "applies": "worker:rbh",
        "help": "Tokens deployed straight to Uniswap V2 rather than through a "
                "launchpad.",
    },
    "RBH_V3_ENABLED": {
        "label": "Uniswap V3 pools", "kind": "bool",
        "group": "Robinhood Detection Sources", "applies": "worker:rbh",
        "help": "Same, on V3.",
    },
    "RBH_V4_ENABLED": {
        "label": "Uniswap V4 pools", "kind": "bool",
        "group": "Robinhood Detection Sources", "applies": "worker:rbh",
        "help": "Same, on V4.",
    },
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(key: str, raw) -> object:
    """Validate `raw` against the field's declared type. Raises ValueError."""
    spec = EDITABLE[key]
    kind = spec.get("kind", "text")
    text = str(raw).strip()

    if kind == "bool":
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"{key} must be true or false")

    if kind in ("wss", "http"):
        # An empty value clears the slot, which is how a fallback is removed.
        # A primary may not be emptied — that blinds whatever it feeds, and
        # replacing one is a save, not a clear.
        if not text:
            # Any fallback slot may be emptied — matched on "_FALLBACK" anywhere
            # rather than as a suffix, or _FALLBACK2 would not count as one.
            if "_FALLBACK" in key:
                return ""
            raise ValueError(f"{key} cannot be empty — it is the primary "
                             f"endpoint; save a replacement URL instead")
        if kind == "wss" and not text.startswith(("ws://", "wss://")):
            raise ValueError(f"{key} must start with wss:// (or ws://) — an "
                             f"https:// URL is the HTTP endpoint, not the socket")
        if kind == "http" and not text.startswith(("http://", "https://")):
            raise ValueError(f"{key} must start with http:// or https:// — a "
                             f"wss:// URL is the WebSocket endpoint, not this one")
        if " " in text:
            raise ValueError(f"{key} must not contain spaces")
        return text

    if kind in ("number", "int"):
        try:
            value = float(text) if kind == "number" else int(text)
        except ValueError:
            raise ValueError(f"{key} must be a {'number' if kind == 'number' else 'whole number'}")
        low = spec.get("min")
        if low is not None and value < low:
            raise ValueError(f"{key} must be at least {low}")
        high = spec.get("max")
        if high is not None and value > high:
            raise ValueError(f"{key} must be at most {high}")
        return value

    if not text:
        raise ValueError(f"{key} cannot be empty")
    if "\n" in text or "\r" in text:
        raise ValueError("value must be a single line")
    return text


def _as_env_text(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"


def _mask_url(value: str) -> str:
    """Hide the API key in an endpoint URL but keep the host readable.

    Used for both "wss" and "http" kind fields — the generic mask would return
    `wss:••••••bC3d`, which tells you nothing, and the one thing you need to
    know before adding a fallback is which provider the primary already uses,
    since a second endpoint on the same account shares the same quota.
    """
    if not value:
        return ""
    scheme, sep, rest = value.partition("://")
    if not sep:
        return _mask(value)
    host, slash, path = rest.partition("/")
    if not path:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}/{_mask(path)}"


def read_values() -> dict[str, dict]:
    """Current values for the editable keys (secrets masked)."""
    out: dict[str, dict] = {}
    for key, spec in EDITABLE.items():
        raw = getattr(settings, key.lower(), "")
        text = _as_env_text(raw) if raw not in ("", None) else ""
        kind = spec.get("kind", "text")
        secret = bool(spec.get("secret"))
        if not secret:
            shown = text
        elif kind in ("wss", "http"):
            shown = _mask_url(text)
        else:
            shown = _mask(text)
        out[key] = {
            "label": spec["label"],
            "kind": kind,
            "group": spec.get("group", "Other"),
            "help": spec.get("help", ""),
            "applies": spec.get("applies", "live"),
            "secret": secret,
            "value": shown,
            "set": bool(text),
        }
    return out


def update(key: str, value) -> object:
    """Replace `key` in .env with `value` (append if absent). Atomic.

    Returns the coerced value so the caller applies exactly what was written.
    """
    if key not in EDITABLE:
        raise KeyError(f"{key} is not editable")
    coerced = _coerce(key, value)
    text = _as_env_text(coerced)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            # Replace the old value; keep any trailing comment on that line.
            comment = ""
            m = re.search(r"\s+#.*$", line)
            if m:
                comment = m.group(0)
            lines[i] = f"{key}={text}{comment}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={text}")

    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)
    return coerced


# How many WSS slots each chain's pool has. ETH and Robinhood share one job per
# socket and get a third for it; SOL's WSS pair is a different provider family
# (needs logsSubscribe) with a much shorter list of candidates, so it stays at 2.
_WSS_SLOT_SUFFIXES = {
    "ETH": ("", "_fallback", "_fallback2"),
    "RBH": ("", "_fallback", "_fallback2"),
    "SOL": ("", "_fallback"),
    "RBHX": ("", "_fallback", "_fallback2"),
}


def _refresh_derived(scfg, key: str) -> None:
    """Rebuild config values computed from other keys at import time.

    Setting scfg.<KEY> is not enough for the RPC endpoints. `SOL_WSS_ENDPOINTS`
    and its ETH/RBH twins (and SOL_HTTP_ENDPOINTS) are lists built once from a
    primary plus fallback(s), and `_FALLBACK` is not itself a scfg attribute —
    so without this a fallback saved in Settings lands in .env, reports
    success, and is never dialled. Same for GAS_RPC_WSS, which falls back to
    the ETH socket.
    """
    if key.startswith(("ETH_RPC_HTTP", "RBH_RPC_HTTP", "SOL_RPC_HTTP", "BNB_RPC_HTTP")):
        chain = key[:3]
        low = chain.lower()
        setattr(scfg, f"{chain}_HTTP_ENDPOINTS",
               [u for u in (getattr(settings, f"{low}_rpc_http", "") or "",
                           getattr(settings, f"{low}_rpc_http_fallback", "") or "")
                if u])
        return
    if key.startswith("RBHX_RPC_WSS"):
        slots = [getattr(settings, f"rbhx_rpc_wss{sfx}", "") or ""
                 for sfx in _WSS_SLOT_SUFFIXES["RBHX"]]
        live = [u for u in slots if u]
        # Blank slots borrow Robinhood's, which is how it runs before its own
        # endpoints are set — and what the "own endpoints" flag reports.
        scfg.RBHX_WSS_ENDPOINTS = live or list(scfg.RBH_WSS_ENDPOINTS)
        scfg.RBHX_OWN_ENDPOINTS = bool(live)
        return
    if not key.startswith(("ETH_RPC_WSS", "RBH_RPC_WSS", "SOL_RPC_WSS")):
        return
    chain = key[:3]
    low = chain.lower()
    slots = [getattr(settings, f"{low}_rpc_wss{suffix}", "") or ""
             for suffix in _WSS_SLOT_SUFFIXES[chain]]
    setattr(scfg, f"{chain}_WSS_ENDPOINTS", [u for u in slots if u])
    if chain == "ETH":
        scfg.GAS_RPC_WSS = settings.gas_rpc_wss or slots[0]
        scfg.GAS_WSS_ENDPOINTS = [u for u in (scfg.GAS_RPC_WSS,
                                              settings.gas_rpc_wss_fallback) if u]
    if chain == "SOL":
        # SOL's WSS applies "live" — no worker restart on save, unlike ETH/RBH
        # (restarting the whole SOL worker would also drop the GMGN feed's
        # in-memory state). Without this kick, a newly saved fallback would
        # only take effect once the current backoff happened to expire, up to
        # 60s later — technically "no restart needed" but not what "paste a
        # key and it just works" means in practice.
        try:
            from . import supervisor
            supervisor.kick_sol_discovery()
        except Exception as exc:  # noqa: BLE001
            log.error(f"could not kick SOL discovery after endpoint change: {exc}")


def apply_runtime(key: str, value) -> None:
    """Push the new value into the live process so it takes effect immediately.

    Updates os.environ, the cached Settings object and the scanner config shim.
    The typed value goes in, not the raw string — the scanners do arithmetic and
    comparisons on these.
    """
    coerced = value if not isinstance(value, str) else _coerce(key, value)
    os.environ[key] = _as_env_text(coerced)
    try:
        object.__setattr__(settings, key.lower(), coerced)
    except Exception:
        setattr(settings, key.lower(), coerced)
    try:
        from .scanners import scfg
        if hasattr(scfg, key):
            setattr(scfg, key, coerced)
        _refresh_derived(scfg, key)
    except Exception as exc:  # noqa: BLE001
        # The value is in .env but the running process did not take it. Saying
        # so matters most for the GMGN fingerprint: the whole point of that
        # form is that the fix applies without a restart.
        log.error(f"{key} written to .env but could not be applied live: {exc}")


def worker_for(key: str) -> str | None:
    """Which scanner must be restarted for this key, if any."""
    applies = EDITABLE.get(key, {}).get("applies", "live")
    return applies.split(":", 1)[1] if applies.startswith("worker:") else None
