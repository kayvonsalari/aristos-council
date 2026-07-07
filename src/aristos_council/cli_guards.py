"""CLI input guards — the paste-slip lesson (2026-07-06).

A doubled paste put a shell path (``examples/snapshot_consensus.py``) into the ticker
argv. Normalized, that becomes a plausible-looking symbol that yfinance can't resolve,
which used to land a SILENT ``UNRATEABLE`` row in the PERMANENT snapshot record. These
pure guards make the CLIs reject such input LOUDLY, before any adapter runs or any row
is written. Pure functions so they unit-test without argparse or a network.
"""

from __future__ import annotations

from typing import Optional

# Substrings/shapes a real ticker never has. Tickers legitimately carry '.' (BRK.B,
# 000660.KS) and '-' (BRK-B), so those are NOT rejected wholesale — only path
# separators, a '.py' script tail, and leading-dash flags.
def implausible_ticker_reason(raw: str) -> Optional[str]:
    """Why ``raw`` cannot be a ticker (a human string), or None if it looks like one.

    Catches the paste-slip shapes: a path separator, a ``.py`` script tail, and a token
    that is really a flag. A blank token is NOT flagged here (the CLIs handle 'no
    tickers' separately)."""
    t = (raw or "").strip()
    if not t:
        return None
    if "/" in t or "\\" in t:
        return "contains a path separator (looks like a file path, not a ticker)"
    if ".py" in t.lower():
        return "contains '.py' (looks like a script path, not a ticker)"
    if t.startswith("-") and len(t) > 1:
        return "starts with '-' (looks like a flag, not a ticker)"
    return None


def find_implausible(tokens) -> Optional[tuple[str, str]]:
    """The first ``(token, reason)`` that cannot be a ticker, or None if all are clean."""
    for t in tokens:
        why = implausible_ticker_reason(t)
        if why is not None:
            return t, why
    return None


def universe_args_error(tickers, universe_id: Optional[str]) -> Optional[str]:
    """The snapshot CLI's input-guard message, or None when the args are clean.

    Two rules: (1) reject any implausible positional ticker token — NAMING it — so a
    pasted path never becomes a silent UNRATEABLE row; (2) positional tickers and
    ``--universe-id`` are MUTUALLY EXCLUSIVE (an error, never silent precedence)."""
    bad = find_implausible(tickers or [])
    if bad is not None:
        tok, why = bad
        return (f"implausible ticker token {tok!r}: {why} — refusing to run "
                "(it would otherwise write a bogus row into the permanent record)")
    if tickers and universe_id:
        return ("pass EITHER positional tickers OR --universe-id, not both "
                "(mutually exclusive — no silent precedence)")
    return None
