"""freeze_dir wiring (ITEM 1).

The UI universe-run path must FREEZE its inputs so Company Check's reference-cohort
reader (`_latest_reference_run`) can replay a past run offline. Before this fix the UI
never passed freeze_dir, `runs/` was never written, and the cohort path was dead code.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.company_check import _latest_reference_run
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import run_rank_pipeline

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0] * 4, tax_provision=[600.0] * 4,
              pretax_income=[2900.0] * 4, invested_capital=[5000.0] * 4),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0] * 4, tax_provision=[300.0] * 4,
              pretax_income=[1450.0] * 4, invested_capital=[5000.0] * 4),
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_freeze_writes_manifest_and_company_check_reader_finds_it(tmp_path):
    runs = tmp_path / "runs"
    result = run_rank_pipeline(
        ["A", "B"], "magic_formula_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_Adapter(), today=date(2026, 6, 30), freeze_dir=runs)

    run_id = result.meta["run_id"]
    assert run_id and (runs / run_id / "manifest.json").exists()

    # Company Check's reader locates that frozen run for the same strategy + universe.
    found = _latest_reference_run(runs, "magic_formula_v1", ["A", "B"])
    assert found is not None and found[0] == run_id


def test_ui_universe_run_wires_freeze_dir():
    # Regression guard for the specific bug: the UI run path must pass freeze_dir, else
    # runs/ is never written and cohort context stays dead.
    pytest.importorskip("streamlit")
    import inspect

    import app
    src = inspect.getsource(app.render_universe_tab)
    assert "freeze_dir" in src
