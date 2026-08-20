"""Shared constants, patterns and the logger every userbot module uses.

The logger is deliberately named "app.scanners.forwarder" rather than after
this module: slog maps a logger's last path segment to the service name shown
on the dashboard's Logs page, and every log line this userbot has ever written
shows up there as "Forwarder". Splitting one file into a package must not
rename them, so all submodules import this one `log`.
"""

from __future__ import annotations

import re

from app.scanners import scfg as config
from app.scanners.slog import get_logger

log = get_logger("app.scanners.forwarder")

# ── Telegram routing (from .env; None = that route is skipped) ─────────────────
DEST_OTTO               = config.DEST_OTTO
DEST_SIGNALS            = config.DEST_SIGNALS
DEST_DEXS               = config.DEST_DEXS
DEST_PREMIUM_ALL        = config.DEST_PREMIUM_ALL
DEST_IC                 = config.DEST_IMPORTANT_CALLER

SOURCE_OTTO   = config.SOURCE_OTTO
SOURCE_DEXS   = config.SOURCE_DEXS
SOURCE_CALL   = config.SOURCE_CALL
SOURCE_BUYBOT = config.SOURCE_BUYBOT

# ── Registry service id gating each handler ────────────────────────────────────
# One id per source channel, named after the channel itself (SOURCE_* in .env),
# so a switch in Settings, a row on the Forwarder page and a log line all use
# the same word for the same thing.
GATE_CALL        = "callanalyser2"         # SOURCE_CALL
GATE_BUYBOT      = "buybottracker"         # SOURCE_BUYBOT
GATE_DEXS        = "dexsignalcall"         # SOURCE_DEXS
GATE_OTTO        = "eth_otto_group"        # SOURCE_OTTO
GATE_PREMIUM     = "premium_callers_signal"  # premium groups → per-chain signal groups
GATE_PREMIUM_ETH = "premium_eth_detection"   # premium groups → ETH detections panel
GATE_PREMIUM_RBH = "premium_rbh_detection"   # premium groups → RBH detections panel
GATE_PREMIUM_SOL = "premium_sol_detection"   # premium groups → SOL detections panel
GATE_PREMIUM_BNB = "premium_bnb_detection"   # premium groups → BNB detections panel
GATE_PREMIUM_BASE = "premium_base_detection"  # premium groups → Base detections panel
# The Second Dashboard's own two switches. Separate from the panel gates
# above: the merged panel and the per-call feed are different readings of the
# same messages, and either can be wanted without the other.
GATE_CALLS      = "second_dashboard_calls"    # one row per call
GATE_CALLS_TG   = "second_dashboard_tracker"  # message text, replies and images
GATE_IC          = "important_caller"        # starred groups → Important Caller

# Every chain the premium panels hold. The daily rollover iterates this, and it
# used to be a hardcoded ("eth", "rbh") — so when SOL and BNB detection were
# added their rows were never archived and never cleared, and grew without
# bound in premium_detections while never appearing in History.
DETECTION_CHAINS = ("eth", "rbh", "bnb", "sol")

# ── Tunables ──────────────────────────────────────────────────────────────────
# How often the live premium-group set is re-read from Mongo.
PREMIUM_RELOAD_SECONDS = 20
# Cap on the per-message dedup guards. ~50k ids is days of traffic.
DEDUP_MAX = 50000
# How many rows a detection panel holds before the daily rollover archives it.
DETECTED_MAX = 300

# ── Patterns ──────────────────────────────────────────────────────────────────
HASH_RE = re.compile(r"#([a-fA-F0-9]{8})")
ETH_RE  = re.compile(r"0x[a-fA-F0-9]{40}", re.IGNORECASE)
# Solana base58 mint/address (32-44 chars, base58 alphabet — no 0 O I l).
# Word-bounded so it doesn't slice a substring out of a longer token.
SOL_RE  = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
