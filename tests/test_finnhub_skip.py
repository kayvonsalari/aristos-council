"""FINNHUB-SKIP-1 — do not call Finnhub for listings the plan cannot serve.

Colab check, 2026-09-18, production key: every endpoint (quote, profile, company-news,
recommendation, peers) returns 200 for FTI and 403 for SBMO.AS and AKSO.OL. The plan is
US-only for ALL data, not just news.

The oil services run made five calls per non-US name to rediscover that and then put
"HTTP 403" in the dark-channel table, which reads like an outage. It is not an outage; it
is a subscription boundary. A boundary is stated, not probed.

The assertion that matters in this file is the NEGATIVE one: zero HTTP calls. A reason
string is easy to get right and easy to get right while still making the call.
"""
from __future__ import annotations

import pytest

from aristos_council.data.finnhub_adapter import (FinnhubAdapter, is_non_us_symbol,
                                                  non_us_reason, skip_non_us)
from aristos_council.data.sentiment import SentimentDataUnavailable, skipped_non_us


class _CountingAdapter(FinnhubAdapter):
    """A FinnhubAdapter whose socket is a counter. Any call is a test failure."""

    def __init__(self):
        super().__init__(api_key="test-key")
        self.http_calls: list[str] = []

    def _open(self, url):                       # pragma: no cover - must never run
        self.http_calls.append(url)
        raise AssertionError("an HTTP call was made for a symbol the plan cannot serve")


def _adapter(monkeypatch) -> _CountingAdapter:
    adapter = _CountingAdapter()

    def _boom(url, timeout=None):               # pragma: no cover - must never run
        adapter.http_calls.append(str(url))
        raise AssertionError("urlopen was reached")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    return adapter


# =========================================================================== #
# 1. classification
# =========================================================================== #
@pytest.mark.parametrize("ticker", ["SBMO.AS", "AKSO.OL", "SPM.MI", "2883.HK", "7203.T",
                                    "RY.TO", "SHEL.L", "AI.PA", "AMS.MC", "SAP.DE",
                                    "NESN.SW", "VOE.VI", "PKN.WA"])
def test_an_exchange_suffix_means_non_us(ticker):
    assert is_non_us_symbol(ticker)
    assert skip_non_us(ticker)


@pytest.mark.parametrize("ticker", ["FTI", "MSFT", "GOOGL", "BRK-B", "V"])
def test_a_bare_symbol_is_us_and_is_still_called(ticker):
    """BRK-B carries a hyphen, not a suffix — it must not be swept up."""
    assert not is_non_us_symbol(ticker)
    assert skip_non_us(ticker) == ""


def test_the_reason_names_the_plan_and_the_ticker_rather_than_an_http_code():
    reason = non_us_reason("SBMO.AS")
    assert reason == ("Finnhub data is US-only on the current plan; not requested for "
                      "SBMO.AS")
    assert "403" not in reason and "HTTP" not in reason


# =========================================================================== #
# 2. the negative assertion: no socket is opened
# =========================================================================== #
def test_a_non_us_symbol_makes_ZERO_http_calls(monkeypatch):
    adapter = _adapter(monkeypatch)
    from datetime import date

    with pytest.raises(SentimentDataUnavailable) as exc:
        adapter.get_company_news("SBMO.AS", start=date(2026, 9, 1), end=date(2026, 9, 18))

    assert adapter.http_calls == [], "the whole point is that nothing was requested"
    assert "US-only on the current plan" in str(exc.value)
    assert "SBMO.AS" in str(exc.value)


def test_every_endpoint_is_covered_not_just_news(monkeypatch):
    """Guarded at the ONE transport seam, so an endpoint added later is covered too."""
    adapter = _adapter(monkeypatch)
    with pytest.raises(SentimentDataUnavailable):
        adapter.get_recommendation_trends("AKSO.OL")
    assert adapter.http_calls == []


def test_a_us_symbol_still_reaches_the_transport(monkeypatch):
    """The other half of the contract: FTI is not skipped, it is called."""
    adapter = _adapter(monkeypatch)
    with pytest.raises(AssertionError, match="urlopen was reached"):
        adapter.get_recommendation_trends("FTI")
    assert len(adapter.http_calls) == 1


# =========================================================================== #
# 3. the switch, for the day the plan changes
# =========================================================================== #
def test_the_env_switch_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("FINNHUB_NON_US", "1")
    assert skip_non_us("SBMO.AS") == ""

    adapter = _adapter(monkeypatch)
    with pytest.raises(AssertionError, match="urlopen was reached"):
        adapter.get_recommendation_trends("SBMO.AS")
    assert len(adapter.http_calls) == 1


@pytest.mark.parametrize("value", ["0", "", "no", "off"])
def test_the_switch_is_off_unless_it_is_really_on(monkeypatch, value):
    monkeypatch.setenv("FINNHUB_NON_US", value)
    assert skip_non_us("SBMO.AS")


# =========================================================================== #
# 4. the record
# =========================================================================== #
def test_the_skipped_names_are_recorded_sorted_and_deduplicated():
    names = ["FTI", "SBMO.AS", "AKSO.OL", "SBMO.AS", "TS", "SPM.MI"]
    assert skipped_non_us(names) == ["AKSO.OL", "SBMO.AS", "SPM.MI"]


def test_nothing_is_recorded_when_the_switch_is_on(monkeypatch):
    monkeypatch.setenv("FINNHUB_NON_US", "1")
    assert skipped_non_us(["SBMO.AS", "AKSO.OL"]) == []


def test_an_all_us_cohort_records_an_empty_list_not_a_missing_key():
    assert skipped_non_us(["FTI", "MSFT"]) == []


# =========================================================================== #
# 5. a plan boundary is not an outage
# =========================================================================== #
def test_the_skip_is_DATA_ABSENT_which_does_not_degrade_a_run():
    """A datum that does not exist FOR US is honest absence, not a broken tool — and
    FailureKind draws exactly that line already."""
    from aristos_council.state import _DEGRADING_FAILURES, FailureKind

    assert FailureKind.DATA_ABSENT not in _DEGRADING_FAILURES
    assert FailureKind.FETCH_ERROR in _DEGRADING_FAILURES
