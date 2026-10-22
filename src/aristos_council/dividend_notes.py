"""EQUINOR-CHECK-1 - one plain sentence beside a dividend-cut reason that is the end of an extraordinary
dividend paid inside the regular payments (``data/extraordinary_dividends.yaml``).

The cut rule's verdict stands: the payment history has one amount per payment and cannot separate the
extraordinary part. The sentence only says so. Driven by a small dated, reasoned list, never by guessing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_FILE = Path(__file__).resolve().parents[2] / "data" / "extraordinary_dividends.yaml"
_CACHE: Optional[tuple] = None
CUT_MARK = "the dividend was cut in"


def _load(path=_FILE) -> tuple[str, set]:
    try:
        import yaml
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001 - display data, never fatal
        return "", set()
    note = " ".join(str(doc.get("note") or "").split())
    tickers = {str(t).upper() for c in (doc.get("companies") or []) for t in (c.get("tickers") or [])}
    return note, tickers


def extraordinary_dividend_note(ticker: str, reason: str, *, path=None) -> str:
    """The sentence for ``ticker`` when ``reason`` is a dividend-cut reason ("" otherwise)."""
    global _CACHE
    if CUT_MARK not in (reason or "").lower():
        return ""
    if path is not None:
        note, tickers = _load(path)
    else:
        if _CACHE is None:
            _CACHE = _load()
        note, tickers = _CACHE
    return note if str(ticker or "").strip().upper() in tickers else ""
