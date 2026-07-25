"""Whole-word keyword matching — the single source of truth for the rule.

Used by both the Settings router (add/remove/test) and the forwarder's premium
detection. Matching is EXACT whole-word, case-insensitive: keyword "ai" matches
"new ai agent" / "AI token" / "ai-agent" but NOT "main road".
"""

from __future__ import annotations

import re


def compile_keyword(word: str) -> str:
    r"""Regex source that matches `word` only as a standalone word: \bword\b."""
    return rf"\b{re.escape(word)}\b"


def keyword_matches(word: str, text: str) -> bool:
    if not word or not text:
        return False
    return re.search(compile_keyword(word), text, re.IGNORECASE) is not None


def match_any(keywords: list[str], text: str) -> str:
    """Return the matched keyword(s) joined by ', ' (longest-first), '' if none."""
    hits: list[str] = []
    for kw in sorted((k for k in keywords if k), key=len, reverse=True):
        if keyword_matches(kw, text) and not any(kw.lower() == h.lower() for h in hits):
            hits.append(kw)
    return ", ".join(hits)
