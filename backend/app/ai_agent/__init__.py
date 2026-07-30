"""Judge a new pump.fun launch by the X account and post behind it.

pump.fun produces thousands of tokens a day and almost all of them are noise.
What separates the handful worth looking at is not on-chain: it is whether a
real, verified account is behind the launch, and what the post it points at is
actually about. So each launch's X link is read and put to Grok, and a match
becomes an alert.

Two questions, asked in two different places on purpose:

  which narrative — every launch that clears the gates, automatically. One
                    question, one call, sixteen narratives to choose from.
  is it real      — Fact check, per token, when somebody presses the button.
                    It is the answer a person acts on, so it is asked while
                    they are looking rather than bought for the thousands of
                    rows nobody opens.

Separately from either, a launch is watched for its first minute; one from a
link that carried five launches inside five minutes and that crossed the market
cap bar in that minute is what reaches Telegram.

What this deliberately does not do:

  • It never calls gmgn.ai at all. The launches come from PumpPortal's socket
    and the socials from each token's own metadata URI, so the scanners' GMGN
    budget and its hard-won Cloudflare pacing are untouched by anything here.
  • It never makes an AI call it can avoid. One X link gets reused across a
    stream of copycat tokens, so links and names are deduplicated first; the
    reference bot learned that the hard way.
  • It sends nothing while AI_DRY_RUN is on. Decisions are recorded either way,
    so a day of them can be read before it is allowed to post.

── Layout ────────────────────────────────────────────────────────────────────

This was one 1366-line module. The pipeline runs in one direction, so the
files follow it and each imports only from the ones above it — no cycles:

    common.py     the narrative list, the tunables, small helpers
    grok.py       both prompts and the one JSON call they share
    notify.py     the Telegram message bodies and senders
    tgfilter.py   the burst + market-cap rule deciding what is worth sending
    judging.py    asking the model, recording the verdict, the watch() loop
    feed.py       PumpPortal ingestion into the X Links rows, and drop counting
    reporting.py  read-only queries the dashboard calls

The names below are the module's public surface — what routers, the supervisor
and seeding import. They are re-exported here so `ai_agent.watch()` and the
rest keep working exactly as before the split.
"""

from .common import DEFAULT_NARRATIVES, load_narratives, narratives
from .grok import ask_grok, fact_check
from .judging import run_once, watch
from .feed import x_feed_watch
from .reporting import (decision_dates, drops, feed_audit, recent, stats,
                        why_dropped, x_link_dates, x_links)

__all__ = [
    # narratives — seeded at startup, edited from Settings
    "DEFAULT_NARRATIVES", "load_narratives", "narratives",
    # the model
    "ask_grok", "fact_check",
    # the loops the supervisor owns
    "watch", "run_once", "x_feed_watch",
    # dashboard reads
    "decision_dates", "drops", "feed_audit", "recent", "stats",
    "why_dropped", "x_link_dates", "x_links",
]
