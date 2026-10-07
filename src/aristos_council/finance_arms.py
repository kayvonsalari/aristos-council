"""FINANCE-ARM-1 (Batch 22 B11) - the one muted note on a company whose debt is mostly a captive finance arm's.

Ford's "owes $141.2bn net of cash ... 11.3 years of free cash flow to repay its debt" includes Ford
Credit, whose borrowings fund customers' car loans. The note says so. NO calculation changes: it is a
sentence under the number, driven by a small dated, reasoned list (``data/finance_arms.yaml``) and never
by guessing from a name.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_FILE = Path(__file__).resolve().parents[2] / "data" / "finance_arms.yaml"
_CACHE: Optional[tuple] = None


def _load(path=_FILE) -> tuple[str, dict]:
    """``(note, {TICKER: company name})``; an absent or unreadable file means no note anywhere."""
    try:
        import yaml
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001 - display data, never fatal
        return "", {}
    note = " ".join(str(doc.get("note") or "").split())
    tickers = {str(t).upper(): str(c.get("name") or "")
               for c in (doc.get("companies") or []) for t in (c.get("tickers") or [])}
    return note, tickers


def finance_arm_note(ticker: str, *, path=None) -> str:
    """The note for ``ticker`` ("" when the company is not on the list)."""
    global _CACHE
    if path is not None:
        note, tickers = _load(path)
    else:
        if _CACHE is None:
            _CACHE = _load()
        note, tickers = _CACHE
    return note if str(ticker or "").strip().upper() in tickers else ""
