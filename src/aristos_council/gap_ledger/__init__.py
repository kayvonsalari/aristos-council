"""GAP-LEDGER-1 — a pre-market news-movers screener that keeps score of itself.

A daily shortlist of US stocks moving pre-market on news, where the maths picks the names
and a model may only write one optional line of prose about headlines it was handed. No
trading, no recommendations, no verdicts — the output is a list and a record, not advice.

The five steps, one module each:

``universe``   the pool (the local market index, read-only) and the liquidity pre-filter
``screen``     the gap, the relative pre-market volume, the spread — all the arithmetic
``news``       EODHD headlines for the candidates, 18h back
``explain``    the optional one-line reason, and the fence around the model
``ledger``     the day's CSV and the seeded control group
``outcomes``   the four after-the-close readings
``score``      candidates vs control group at each checkpoint, with a day count

``run.run_screen`` is the single entry the CLI and the tests both use; ``run.format_report``
is what the CLI prints, so there is one description of a run rather than two that drift.

It lives beside Aristos and shares its adapters, its market index and its ``.env`` — and
nothing else. No Aristos surface imports this package, no council is convened, no strategy
is loaded, and no verdict is written: a gap is a fact about a morning, not a thesis.

This package is deliberately kept out of ``app.py``. Its own read-only Streamlit entry
point is ``gap_ledger_app.py`` at the repo root.
"""
from __future__ import annotations

from .config import DEFAULT_CONFIG, DEFAULT_ROOT, NY, GapConfig
from .ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow, ledger_days, read_day
from .run import RunResult, format_report, run_screen
from .score import Scorecard, score

__all__ = [
    "DEFAULT_CONFIG", "DEFAULT_ROOT", "NY", "GapConfig",
    "GROUP_BASELINE", "GROUP_CANDIDATE", "LedgerRow", "ledger_days", "read_day",
    "RunResult", "format_report", "run_screen",
    "Scorecard", "score",
]
