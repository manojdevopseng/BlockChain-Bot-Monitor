"""Safe read/update of backend/.env from the dashboard.

Only the keys listed in `EDITABLE` may be changed, and the file is rewritten
atomically (temp file + os.replace) preserving every other line, comment and
ordering. A missing key is appended rather than silently dropped.

Used by the Settings page so credentials that expire (the GMGN web fingerprint)
can be refreshed without SSH-ing into the EC2 box.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import ROOT, settings

ENV_PATH = ROOT / ".env"

# key -> (label, secret?) — anything not listed here can never be written.
EDITABLE: dict[str, tuple[str, bool]] = {
    "GMGN_CLIENT_ID": ("GMGN Client ID", False),
    "GMGN_DEVICE_ID": ("GMGN Device ID", False),
    "GMGN_FP_DID": ("GMGN Fingerprint (fp_did)", False),
    "CF_CLEARANCE": ("Cloudflare clearance cookie", True),
    "GMGN_API_KEY": ("GMGN API key", True),
}


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"


def read_values() -> dict[str, dict]:
    """Current values for the editable keys (secrets masked)."""
    out: dict[str, dict] = {}
    for key, (label, secret) in EDITABLE.items():
        raw = getattr(settings, key.lower(), "") or ""
        out[key] = {
            "label": label,
            "secret": secret,
            "value": _mask(raw) if secret else raw,
            "set": bool(raw),
        }
    return out


def update(key: str, value: str) -> None:
    """Replace `key` in .env with `value` (append if absent). Atomic."""
    if key not in EDITABLE:
        raise KeyError(f"{key} is not editable")
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise ValueError("value must be a single line")

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
            lines[i] = f"{key}={value}{comment}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)


def apply_runtime(key: str, value: str) -> None:
    """Push the new value into the live process so it takes effect immediately.

    Updates os.environ, the cached Settings object and the scanner config shim,
    so a running GMGN client picks it up on its next request — no restart.
    """
    os.environ[key] = value
    try:
        object.__setattr__(settings, key.lower(), value)
    except Exception:
        setattr(settings, key.lower(), value)
    try:
        from .scanners import scfg
        if hasattr(scfg, key):
            setattr(scfg, key, value)
    except Exception:
        pass
