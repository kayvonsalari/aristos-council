"""ANALYST-TREND-1 - the analyst forecast direction mark on the Company Check page.

The parser is tested from RECORDED responses (AAPL.US and ENR.XETRA, probed 2026-09-25, trimmed to
the entries that matter): the shape assumed here is the shape the provider serves - values are
strings, several entries are labelled ``0y``, and the current fiscal year is the LATEST-dated one.
No test reaches the network: the fetcher takes an injected opener and a tmp cache directory, and
the tab-level call takes an injected fetcher.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council.abs_readings import (ANALYST_FLAT_BAND, ANALYST_MIN_ANALYSTS,
                                          ANALYST_MIN_BASE, MARK_FALLING, MARK_FLAT,
                                          MARK_RISING, analyst_trend)
from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.data.analyst_trend import (CHARGE_FUNDAMENTALS, SOURCE_TAG,
                                                STALE_AFTER_DAYS, TrendData, TrendPeriod,
                                                fetch_analyst_trend, parse_trend)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "analyst_trend"
TODAY = date(2026, 9, 25)


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _entry(period, end, *, now="8.00", ago="8.00", analysts="20.0000"):
    return {"date": end, "period": period, "earningsEstimateAvg": now, "epsTrendCurrent": now,
            "epsTrend90daysAgo": ago, "earningsEstimateNumberOfAnalysts": analysts}


def _data(now=8.0, ago=8.0, analysts=20, **kw):
    return TrendData(current=TrendPeriod("2026-12-31", now, ago, analysts),
                     as_of="2026-09-25", **kw)


# =========================================================================== #
# the parser, from recorded payloads
# =========================================================================== #
def test_the_current_year_is_the_latest_dated_0y_not_any_entry_labelled_0y():
    """AAPL's fixture carries three '0y' entries (2017, 2025, 2026): a past year keeps the label it
    had when it was current. Only 2026-09-30 is the current fiscal year."""
    data = parse_trend(_load("aapl_us.json"), today=TODAY)
    assert data.available
    assert data.current == TrendPeriod("2026-09-30", 8.8195, 8.7596, 39)
    assert data.next_year == TrendPeriod("2027-09-30", 9.5815, 9.6826, 40)


def test_a_non_us_name_parses_the_same_way():
    data = parse_trend(_load("enr_xetra.json"), today=TODAY)
    assert data.current == TrendPeriod("2026-09-30", 4.468, 4.3541, 19)
    assert data.next_year.period_end == "2027-09-30"


def test_every_absent_shape_says_why():
    assert "no Earnings::Trend block" in parse_trend({}, today=TODAY).note
    assert "no Earnings::Trend block" in parse_trend(None, today=TODAY).note
    assert "no Earnings::Trend block" in parse_trend("[]", today=TODAY).note
    only_quarters = {"2026-06-30": _entry("0q", "2026-06-30")}
    assert "no current-year (0y) estimate" in parse_trend(only_quarters, today=TODAY).note
    assert not parse_trend({}, today=TODAY).available


def test_a_current_year_estimate_that_ended_long_ago_is_stale():
    doc = {"2024-06-30": _entry("0y", "2024-06-30")}
    assert (TODAY - date(2024, 6, 30)).days > STALE_AFTER_DAYS
    assert "stale" in parse_trend(doc, today=TODAY).note
    recent = {"2026-06-30": _entry("0y", "2026-06-30")}       # ended, not yet reported: still current
    assert parse_trend(recent, today=TODAY).available


def test_a_next_year_entry_that_is_not_after_the_current_year_is_ignored():
    doc = {"2026-09-30": _entry("0y", "2026-09-30"), "2025-09-30": _entry("+1y", "2025-09-30")}
    assert parse_trend(doc, today=TODAY).next_year is None


def test_strings_and_sentinels_become_numbers_or_nothing():
    doc = {"2026-09-30": {"date": "2026-09-30", "period": "0y", "epsTrendCurrent": "NA",
                          "earningsEstimateAvg": "3.5000", "epsTrend90daysAgo": None,
                          "earningsEstimateNumberOfAnalysts": "12.0000"}}
    current = parse_trend(doc, today=TODAY).current
    assert current.now == 3.5 and current.ago_90d is None and current.analysts == 12


# =========================================================================== #
# the mark
# =========================================================================== #
def test_the_recorded_apple_year_reads_flat_and_says_where_it_came_from():
    trend = analyst_trend(dataclasses.replace(
        parse_trend(_load("aapl_us.json"), today=TODAY), as_of="2026-09-25", units_charged=10))
    assert trend.mark == MARK_FLAT                        # +0.68% is inside the +/-2% band
    text = trend.direction.text()
    assert "fiscal year ending 2026-09-30" in text and "8.82" in text and "8.76" in text
    assert "39 analysts" in text
    assert f"source: {SOURCE_TAG}, as of 2026-09-25" in text          # the value carries its tags
    assert "next year (fiscal year ending 2027-09-30)" in trend.next_year.text()


@pytest.mark.parametrize("now, mark", [
    (10.30, MARK_RISING), (10.20, MARK_FLAT), (10.05, MARK_FLAT), (10.00, MARK_FLAT),
    (9.80, MARK_FLAT), (9.79, MARK_FALLING), (12.00, MARK_RISING), (7.00, MARK_FALLING)])
def test_the_flat_band_is_plus_or_minus_two_percent_of_the_old_figure_and_inclusive(now, mark):
    assert ANALYST_FLAT_BAND == 0.02
    assert analyst_trend(_data(now=now, ago=10.0)).mark == mark


def test_a_forecast_from_a_negative_base_that_improves_is_rising_not_falling():
    """Relative to |ago|: -0.50 -> -0.20 is a rise of 60%."""
    assert analyst_trend(_data(now=-0.20, ago=-0.50)).mark == MARK_RISING
    assert analyst_trend(_data(now=-0.80, ago=-0.50)).mark == MARK_FALLING


@pytest.mark.parametrize("analysts", [0, 1, 2])
def test_fewer_than_three_analysts_abstains_and_says_so(analysts):
    assert ANALYST_MIN_ANALYSTS == 3
    trend = analyst_trend(_data(now=12.0, ago=10.0, analysts=analysts))
    assert trend.mark == "" and not trend.available
    assert f"only {analysts} analyst(s)" in trend.direction.text()
    assert trend.direction.value is None


def test_three_analysts_is_enough():
    assert analyst_trend(_data(now=12.0, ago=10.0, analysts=3)).mark == MARK_RISING


def test_an_unstated_analyst_count_abstains():
    trend = analyst_trend(_data(analysts=None))
    assert trend.mark == "" and "not stated" in trend.direction.text()


def test_a_missing_ninety_day_figure_abstains_it_is_not_a_zero():
    trend = analyst_trend(TrendData(current=TrendPeriod("2026-12-31", 5.0, None, 20)))
    assert trend.mark == "" and "no 90-days-ago estimate" in trend.direction.text()


def test_a_ninety_day_figure_within_one_cent_of_zero_abstains():
    assert ANALYST_MIN_BASE == 0.01
    for ago in (0.0, 0.005, -0.009):
        trend = analyst_trend(_data(now=1.0, ago=ago))
        assert trend.mark == "" and "within one cent of zero" in trend.direction.text(), ago
    assert analyst_trend(_data(now=1.0, ago=0.01)).mark == MARK_RISING     # exactly a cent: usable


def test_a_missing_current_estimate_abstains():
    trend = analyst_trend(TrendData(current=TrendPeriod("2026-12-31", None, 5.0, 20)))
    assert trend.mark == "" and "no current consensus" in trend.direction.text()


def test_no_current_year_at_all_abstains_with_the_providers_reason_and_still_carries_the_tags():
    trend = analyst_trend(dataclasses.replace(parse_trend({}, today=TODAY), as_of="2026-09-25"))
    text = trend.direction.text()
    assert trend.mark == "" and "no Earnings::Trend block" in text
    assert f"source: {SOURCE_TAG}, as of 2026-09-25" in text


def test_the_next_year_is_shown_even_when_the_mark_abstains():
    data = TrendData(current=TrendPeriod("2026-12-31", 5.0, 5.0, 1),
                     next_year=TrendPeriod("2027-12-31", 6.0, 5.0, 1), as_of="2026-09-25")
    trend = analyst_trend(data)
    assert trend.mark == ""
    assert "6.00" in trend.next_year.text() and "up 20.0%" in trend.next_year.text()


def test_the_cost_is_reported_beside_the_figures():
    paid = analyst_trend(_data(units_charged=CHARGE_FUNDAMENTALS))
    cached = analyst_trend(_data(cached=True))
    none = analyst_trend(_data())
    assert "10 EODHD units charged" in paid.lines()[-1]
    assert "0 EODHD units (cached for today)" in cached.lines()[-1]
    assert "0 EODHD units (no request made)" in none.lines()[-1]


# =========================================================================== #
# the fetcher: day-cache, cost, never raises, never IBKR
# =========================================================================== #
class _Opener:
    """Records every request; serves a recorded payload."""

    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.urls = payload, error, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        outer = self

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(outer.payload).encode("utf-8")
        return _Resp()


def test_a_miss_makes_one_request_charges_ten_units_and_writes_the_day_cache(tmp_path):
    opener = _Opener(_load("aapl_us.json"))
    data = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1 and "filter=Earnings%3A%3ATrend" in opener.urls[0]
    assert "/fundamentals/AAPL.US" in opener.urls[0]
    assert data.units_charged == CHARGE_FUNDAMENTALS == 10 and data.cached is False
    assert data.as_of == "2026-09-25" and data.available
    assert (tmp_path / "eodhd_AAPL.US_2026_09_25_trend.json").exists()


def test_a_second_call_the_same_day_is_a_cache_hit_and_costs_nothing(tmp_path):
    opener = _Opener(_load("aapl_us.json"))
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    again = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1
    assert again.units_charged == 0 and again.cached is True and again.available


def test_the_next_day_asks_again(tmp_path):
    opener = _Opener(_load("aapl_us.json"))
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=date(2026, 9, 26),
                        opener=opener)
    assert len(opener.urls) == 2


def test_no_key_means_no_request_and_no_charge(tmp_path):
    opener = _Opener(_load("aapl_us.json"))
    data = fetch_analyst_trend("AAPL", api_key="  ", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert opener.urls == [] and data.units_charged == 0 and not data.available
    assert "EODHD_API_KEY is not set" in data.note


def test_a_failed_request_is_an_abstention_not_an_exception_and_does_not_leak_the_key(tmp_path):
    opener = _Opener(error=OSError("HTTP 403 https://eodhd.com/api/x?api_token=SECRET"))
    data = fetch_analyst_trend("AAPL", api_key="SECRET", cache_dir=tmp_path, today=TODAY,
                               opener=opener)
    assert not data.available and "request failed (OSError)" in data.note
    assert "SECRET" not in data.note and "SECRET" not in analyst_trend(data).direction.text()
    assert data.units_charged == 10               # the request was made, so it is counted
    assert not list(tmp_path.iterdir())            # nothing cached from a failure


def test_a_corrupt_cache_entry_is_refetched(tmp_path):
    (tmp_path / "eodhd_AAPL.US_2026_09_25_trend.json").write_text("{not json", encoding="utf-8")
    opener = _Opener(_load("aapl_us.json"))
    data = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1 and data.available and data.units_charged == 10


def test_a_name_with_no_trend_block_abstains_visibly_and_is_cached(tmp_path):
    opener = _Opener({})
    data = fetch_analyst_trend("XYZ", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert not data.available and "no Earnings::Trend block" in data.note
    assert data.units_charged == 10


def test_the_source_never_imports_interactive_brokers():
    """IBKR data is licensed for the owner's non-professional use and must not reach client-facing
    output; this module and the reading are EODHD only."""
    import aristos_council.abs_readings as reading
    import aristos_council.data.analyst_trend as source
    for module in (source, reading):
        text = Path(module.__file__).read_text(encoding="utf-8").lower()
        assert "import ibkr" not in text and "from .ibkr" not in text and ".ibkr import" not in text
        assert "gap_ledger" not in text.replace("gap ledger", "")


# =========================================================================== #
# on the Company Check page: a mark only
# =========================================================================== #
from tests.test_company_check import (_MU, STRAT_DIR, UNIV_DIR, _FinAdapter,  # noqa: E402
                                      _freeze_financials_run, _OneName)

_CHECK_DAY = date(2026, 6, 30)
_TRENDS = {
    "rising": _data(now=12.0, ago=10.0),
    "flat": _data(now=10.1, ago=10.0),
    "falling": _data(now=8.0, ago=10.0),
    "abstaining": TrendData(note="EODHD has no Earnings::Trend block for this name",
                            as_of="2026-06-30"),
}


def _fetcher(trend):
    calls = []

    def fetch(ticker, *, today):
        calls.append((ticker, today))
        return trend
    fetch.calls = calls
    return fetch


def _mu_check(**kw):
    return run_company_check("MU", "magic_formula_momentum_v1", "", adapter=_OneName(_MU),
                             strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                             runs_dir=Path("runs"), today=_CHECK_DAY, **kw)


def _without_analyst_block(text: str) -> str:
    out, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("ANALYST FORECAST DIRECTION"):
            skipping = True
            continue
        if skipping and line.startswith("  "):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def test_off_by_default_nothing_is_fetched_and_the_output_is_untouched():
    fetch = _fetcher(_TRENDS["rising"])
    result = _mu_check(analyst_fetcher=fetch)                  # with_analyst_trend not asked for
    assert result.analyst_trend is None and fetch.calls == []
    assert "ANALYST FORECAST" not in format_company_check(result)


@pytest.mark.parametrize("which", sorted(_TRENDS))
def test_the_mark_changes_no_screen_gate_factor_or_verdict_whatever_it_says(which):
    """Ranks and verdicts are byte-identical with the mark on and off: the whole result, but for
    the mark itself, compares equal, and so does the printed report but for the mark's own block."""
    off = _mu_check()
    on = _mu_check(with_analyst_trend=True, analyst_fetcher=_fetcher(_TRENDS[which]))
    assert on.analyst_trend is not None
    assert dataclasses.replace(on, analyst_trend=None) == off
    assert _without_analyst_block(format_company_check(on)) == format_company_check(off)
    assert "ANALYST FORECAST DIRECTION" in format_company_check(on)


def test_the_mark_and_its_abstention_are_both_printed_with_their_source_tag():
    rising = format_company_check(_mu_check(with_analyst_trend=True,
                                            analyst_fetcher=_fetcher(_TRENDS["rising"])))
    assert "forecasts rising" in rising and f"source: {SOURCE_TAG}, as of 2026-09-25" in rising
    quiet = format_company_check(_mu_check(with_analyst_trend=True,
                                           analyst_fetcher=_fetcher(_TRENDS["abstaining"])))
    assert "not stated - EODHD has no Earnings::Trend block" in quiet.replace("\u2014", "-")
    assert "does not vote and changes no verdict" in quiet


def test_the_verdict_of_record_and_cohort_context_are_identical_with_the_mark_on(tmp_path):
    """The frozen-run path: the checked name's rank and verdict are quoted from the run and the mark
    cannot reach them."""
    runs = tmp_path / "runs"
    _freeze_financials_run(runs)

    def check(**kw):
        return run_company_check("GS", "financials_v1", "financials_16_v1",
                                 adapter=_FinAdapter(), strategies_dir=STRAT_DIR,
                                 universes_dir=UNIV_DIR, runs_dir=runs, today=_CHECK_DAY, **kw)
    off = check()
    on = check(with_analyst_trend=True, analyst_fetcher=_fetcher(_TRENDS["falling"]))
    assert off.verdict_of_record and off.verdict_of_record == on.verdict_of_record
    assert [f.context for f in off.factors] == [f.context for f in on.factors]
    assert dataclasses.replace(on, analyst_trend=None) == off


def test_an_unrateable_name_never_spends_a_request():
    from tests.test_company_check import _OneName as OneName
    fetch = _fetcher(_TRENDS["rising"])
    result = run_company_check("PARA", "magic_formula_momentum_v1", "",
                               adapter=OneName(_MU.__class__(ticker="PARA"), has_price=False),
                               strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                               runs_dir=Path("runs"), today=_CHECK_DAY,
                               with_analyst_trend=True, analyst_fetcher=fetch)
    assert result.unrateable and fetch.calls == []


def test_the_tab_and_the_cli_are_the_only_callers_that_ask_for_the_mark():
    """Company Check tab and CLI opt in; nothing else that runs a check does."""
    root = Path(__file__).resolve().parents[1]
    asking = [p.relative_to(root).as_posix() for p in root.rglob("*.py")
              if "with_analyst_trend=" in p.read_text(encoding="utf-8", errors="ignore")
              and not p.relative_to(root).as_posix().startswith(("tests/", "docs/"))]
    assert sorted(asking) == ["app.py", "examples/company_check.py"]
