"""SMALL-WORDING-1 (2026-10-31 sweep): four small wording faults."""
from __future__ import annotations

from types import SimpleNamespace


def test_the_quarter_end_is_the_statements_not_the_52_week_calendar_date():
    """(c) KO's third quarter: the headline date 2026-10-02, the statement 2026-09-30 -> 'Sep 2026'."""
    import pandas as pd
    from aristos_council.data.yfinance_adapter import _quarter_end
    q = pd.DataFrame({pd.Timestamp("2026-09-30"): [1.0]}, index=["Total Debt"])
    assert _quarter_end("2026-10-02", q) == "2026-09-30"
    assert _quarter_end("2026-10-02", None) == "2026-10-02"                       # no statement: the headline stands
    far = pd.DataFrame({pd.Timestamp("2026-03-31"): [1.0]}, index=["Total Debt"])
    assert _quarter_end("2026-10-02", far) == "2026-10-02"                         # not within 15 days: untouched
    from aristos_council.abs_readings import _ttm_basis
    assert _ttm_basis(SimpleNamespace(trailing_period_end="2026-09-30")) == ", twelve months to Sep 2026"


def test_a_list_runs_band_table_does_not_claim_the_ranked_tables_order():
    """(b)"""
    from aristos_council.pipeline import VALUATION_BAND_INTRO, VALUATION_BAND_INTRO_UNION
    assert "Rows follow the ranked table's order" in VALUATION_BAND_INTRO           # true for one lens
    assert "ranked table" not in VALUATION_BAND_INTRO_UNION
    assert "first ranked" in VALUATION_BAND_INTRO_UNION


def test_the_judge_pack_says_which_earnings_the_band_pe_divides_by():
    """(d)"""
    from aristos_council.judges.blind_pack import _valuation
    band = ("P/E 15.7 (market cap over the latest annual net income, not the trailing twelve months) — "
            "94th percentile (dearest) of its own 5-year range")
    rep = SimpleNamespace(check=SimpleNamespace(valuation_band=band, band_percentile=94.0))
    out = " ".join(_valuation(rep))
    assert "latest ANNUAL net income" in out and "trailing P/E stated elsewhere" in out
    ev = SimpleNamespace(check=SimpleNamespace(valuation_band="EV/EBIT 21.9 — 70th percentile of its own 5-year range",
                                               band_percentile=70.0))
    assert "ANNUAL" not in " ".join(_valuation(ev))


def test_no_ratings_but_forecasts_says_so_with_the_vendors_reason():
    """(a) 1211.HK: 'No analyst ratings are shown' while the page used the analysts' EPS forecasts."""
    import re
    src = open("src/aristos_council/company_story.py", encoding="utf-8").read()
    assert "forecasts are shown below" in src and "getattr(trend, \"rows\", None)" in src
