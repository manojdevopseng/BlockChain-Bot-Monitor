"""Safe read/update of backend/.env from the dashboard.

Only the keys listed in `EDITABLE` may be changed, and the file is rewritten
atomically (temp file + os.replace) preserving every other line, comment and
ordering. A missing key is appended rather than silently dropped.

Used by the Settings page so values that need tuning — the GMGN web fingerprint
that expires, the gas-fee threshold, the SOL watch window, the Robinhood V3
source — can be changed without SSH-ing into the EC2 box.

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
    "RBH_V3_ENABLED": {
        "label": "Robinhood — watch Uniswap V3", "kind": "bool", "group": "Detection Tuning",
        "applies": "worker:rbh",
        "help": "Robinhood Chain carries both noxa.fun launches and Uniswap "
                "deployments. With this off, a token launched on V3 is never "
                "seen, so a matching SOL ticker cannot fire.",
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


def read_values() -> dict[str, dict]:
    """Current values for the editable keys (secrets masked)."""
    out: dict[str, dict] = {}
    for key, spec in EDITABLE.items():
        raw = getattr(settings, key.lower(), "")
        text = _as_env_text(raw) if raw not in ("", None) else ""
        secret = bool(spec.get("secret"))
        out[key] = {
            "label": spec["label"],
            "kind": spec.get("kind", "text"),
            "group": spec.get("group", "Other"),
            "help": spec.get("help", ""),
            "applies": spec.get("applies", "live"),
            "secret": secret,
            "value": _mask(text) if secret else text,
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
    except Exception as exc:  # noqa: BLE001
        # The value is in .env but the running process did not take it. Saying
        # so matters most for the GMGN fingerprint: the whole point of that
        # form is that the fix applies without a restart.
        log.error(f"{key} written to .env but could not be applied live: {exc}")


def worker_for(key: str) -> str | None:
    """Which scanner must be restarted for this key, if any."""
    applies = EDITABLE.get(key, {}).get("applies", "live")
    return applies.split(":", 1)[1] if applies.startswith("worker:") else None
