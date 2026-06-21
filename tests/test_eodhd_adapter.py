"""EODHD adapter — dividend history + the calendar-year streak it makes possible.

No live API calls: the HTTP boundary is factored out (``dividend_events_from_rows``
is a pure parser, and ``_get_json`` is overridden in a fake), so every test runs
on recorded fixture JSON — the same fake pattern the rest of the suite uses.

The fixtures encode the traps the Nestlé data exposed: split-adjusted vs raw
values, an annual->Interim/Final cadence change, a genuine cut, short history,
and an incomplete current year.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DataUnavailable, normalize_ticker
from aristos_council.data.eodhd_adapter import (
    EODHDAdapter,
    dividend_events_from_rows,
)
from aristos_council.tools.screening import (
    dividend_growth_streak_by_calendar_year,
    min_growth_streak_criterion_by_year,
)

FULL_RANGE = dict(start=date(1900, 1, 1), end=date(2100, 1, 1))


# --------------------------------------------------------------------------- #
# Fixture builders (EODHD div-endpoint row shape)
# --------------------------------------------------------------------------- #
def _row(d: str, value: float, *, unadjusted: float | None = None,
         period: str | None = "Final") -> dict:
    return {"date": d, "value": value,
            "unadjustedValue": value if unadjusted is None else unadjusted,
            "currency": "USD", "period": period}


def _annual_rows(start_year: int, values: list[float]) -> list[dict]:
    """One Final payment per calendar year, oldest-first."""
    return [_row(f"{start_year + i}-05-15", v) for i, v in enumerate(values)]


# --------------------------------------------------------------------------- #
# Ticker normalization (the SK Hynix trailing-dot bug)
# --------------------------------------------------------------------------- #
def test_normalize_ticker_strips_trailing_dot():
    assert normalize_ticker("000660.KS.") == "000660.KS"   # the live bug
    assert normalize_ticker("  ko.us  ") == "KO.US"         # trim + upper
    assert normalize_ticker("000660.KS. ") == "000660.KS"   # trailing dot + space
    assert normalize_ticker("NESN.SW") == "NESN.SW"         # internal dot kept
    assert normalize_ticker("aapl") == "AAPL"


# --------------------------------------------------------------------------- #
# Pure parser: adjusted value, ordering, range, robustness
# --------------------------------------------------------------------------- #
def test_parser_uses_adjusted_value_not_unadjusted():
    rows = [_row("2002-05-15", value=0.64, unadjusted=6.40)]  # Nestlé split shape
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    assert len(evs) == 1
    assert evs[0].amount == 0.64           # ADJUSTED value, never unadjustedValue


def test_parser_sorts_oldest_first_and_filters_range():
    rows = [_row("2020-05-15", 2.0), _row("2010-05-15", 1.0), _row("2030-05-15", 3.0)]
    evs = dividend_events_from_rows(
        rows, start=date(2009, 1, 1), end=date(2025, 1, 1))
    assert [e.ex_date.year for e in evs] == [2010, 2020]   # 2030 filtered, sorted


def test_parser_skips_malformed_rows():
    rows = [
        _row("2010-05-15", 1.0),
        {"date": "not-a-date", "value": 2.0},
        {"date": "2011-05-15", "value": None},
        {"date": "2012-05-15", "value": -5.0},   # non-positive -> not a real payment
        "garbage",
    ]
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    assert [e.ex_date.year for e in evs] == [2010]


# --------------------------------------------------------------------------- #
# Streak — the six required scenarios (calendar-year SUM of adjusted values)
# --------------------------------------------------------------------------- #
def test_streak_25_years_increasing_passes_20():
    rows = _annual_rows(1998, [1.0 + 0.1 * i for i in range(27)])  # 27 yrs rising
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20
    r = min_growth_streak_criterion_by_year(evs, min_years=20)
    assert r.passed is True


def test_cadence_change_annual_to_interim_final_not_falsely_broken():
    # 2000-2019 one payment/yr; 2020-2024 split into Interim+Final whose YEARLY
    # TOTAL keeps rising. Calendar-year grouping must see the totals increase.
    rows = _annual_rows(2000, [1.0 + 0.1 * i for i in range(20)])  # 2000..2019
    year_total = 2.9
    for y in range(2020, 2025):
        year_total += 0.2
        interim, final = year_total * 0.4, year_total * 0.6
        rows.append(_row(f"{y}-03-01", round(interim, 4), period="Interim"))
        rows.append(_row(f"{y}-09-01", round(final, 4), period="Final"))
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20    # spans the cadence change, no break

    # Contrast: the per-payment MEDIAN method DOES misread the cadence change as a
    # cut (each Interim is far below the prior single annual payment) — which is
    # exactly why EODHD uses the calendar-year method.
    from aristos_council.tools.screening import consecutive_dividend_growth_years
    per_payment, _ = consecutive_dividend_growth_years(evs)
    assert per_payment < streak


def test_split_divergence_uses_adjusted_no_false_break():
    # Adjusted `value` rises smoothly; unadjustedValue has a 10x split jump down
    # at 2002. Using the adjusted value, there is no phantom break.
    values = [1.0 + 0.1 * i for i in range(25)]       # smooth adjusted series
    rows = []
    for i, v in enumerate(values):
        year = 2000 + i
        # raw 10x higher pre-split (<=2001), normal after — divergence at 2002
        raw = v * 10 if year <= 2001 else v
        rows.append(_row(f"{year}-05-15", round(v, 4), unadjusted=round(raw, 4)))
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20


def test_genuine_cut_resets_streak_and_fails():
    # 25 yrs of history, but a real cut 3 years before the latest year -> the
    # streak resets at the cut. History is long enough to judge, so this is a
    # genuine FAIL, not NOT-EVAL.
    values = [1.0 + 0.1 * i for i in range(25)]       # 2000..2024 rising...
    values[21] = 0.1                                   # ...except a 2021 cut
    rows = _annual_rows(2000, values)
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak < 20          # reset by the cut
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is False


def test_short_history_is_not_eval_not_a_fail():
    rows = _annual_rows(2015, [1.0 + 0.1 * i for i in range(10)])  # only 10 yrs
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, note = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is None                              # NOT-EVAL
    assert "insufficient history" in note
    # feeds the INSUFFICIENT_EVIDENCE path: passed is None, never False
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is None


def test_incomplete_current_year_excluded_not_read_as_cut():
    # 24 complete increasing years, then a current year with ONLY an Interim
    # payment (well below the prior full-year total). Dropping the latest year
    # means the partial year is NOT read as a cut.
    rows = _annual_rows(2000, [1.0 + 0.1 * i for i in range(24)])  # 2000..2023
    rows.append(_row("2024-03-01", 0.2, period="Interim"))         # partial 2024
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20         # partial year did not break it
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is True


# --------------------------------------------------------------------------- #
# Adapter: error mapping, never a silent zero
# --------------------------------------------------------------------------- #
class _FakeEODHD(EODHDAdapter):
    """EODHDAdapter with the HTTP boundary stubbed by canned rows."""

    def __init__(self, rows):
        super().__init__(api_key="test-key")
        self._rows = rows

    def _get_json(self, path):           # override the only networked method
        return self._rows


def test_adapter_parses_rows_into_events():
    rows = _annual_rows(2018, [1.0, 1.1, 1.2, 1.3])
    evs = _FakeEODHD(rows).get_dividend_history("KO.US", **FULL_RANGE)
    assert [e.ex_date.year for e in evs] == [2018, 2019, 2020, 2021]
    assert evs[0].amount == 1.0


def test_adapter_empty_array_is_data_unavailable_not_silent_zero():
    with pytest.raises(DataUnavailable):
        _FakeEODHD([]).get_dividend_history("KO.US", **FULL_RANGE)


def test_adapter_missing_key_is_data_unavailable(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(DataUnavailable, match="EODHD_API_KEY is not set"):
        EODHDAdapter(api_key=None).get_dividend_history("KO.US", **FULL_RANGE)


def test_adapter_price_and_fundamentals_still_deferred():
    a = EODHDAdapter(api_key="k")
    with pytest.raises(NotImplementedError):
        a.get_price_history("KO.US", start=date(2020, 1, 1), end=date(2021, 1, 1))
    with pytest.raises(NotImplementedError):
        a.get_fundamentals("KO.US")
