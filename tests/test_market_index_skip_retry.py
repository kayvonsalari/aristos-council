"""INDEX-SKIP-RETRY-1 — a network error is not a 404.

MARKET-INDEX-SKIP-1 made one unlistable exchange skippable instead of fatal. It treated every
failure the same, and on 2026-09-23 a network blip made NINE healthy exchanges look unavailable
inside a single second: each listing raised a URLError, was skipped as "not found", and the
build reported nine missing markets that were never missing.

The two are different facts and want different responses:

* HTTP 404 is an ANSWER — the venue is not on this plan, asking again gets the same 404, so it is
  skipped at once and reported as "not available (404)";
* a URLError / timeout / dropped connection is NO answer — a fact about the moment, so it is
  retried on a backoff (3 tries over about two minutes) before the exchange is skipped, and
  reported as "network error, will retry next build".

Only the LISTING call retries. A per-symbol fundamentals call that fails is counted and the
build moves on (a retrying loop there would turn a real outage into a build that never ends), and
a quota refusal still stops everything.

No test reaches a network or sleeps: ``urlopen`` is replaced and ``sleep`` records.
"""
from __future__ import annotations

import http.client
import urllib.error
from datetime import date

import pytest

from aristos_council import market_index as mi


# --------------------------------------------------------------------------- #
# a fake wire
# --------------------------------------------------------------------------- #
class _Wire:
    """Replaces ``urlopen``: answers from a script, one entry per call.

    An entry is either a JSON-able payload (served) or an exception INSTANCE (raised). The last
    entry repeats, so ``[URLError]`` means "always down".
    """

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, url, timeout=None):
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(entry, BaseException):
            raise entry
        return _Response(entry)


class _Response:
    def __init__(self, payload) -> None:
        import json
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _down() -> urllib.error.URLError:
    return urllib.error.URLError("getaddrinfo failed")


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://eodhd.com/api/x", code, f"HTTP {code}", {}, None)


def _source(monkeypatch, *script):
    wire = _Wire(*script)
    monkeypatch.setattr(mi.urllib.request, "urlopen", wire)
    sleeps: list[float] = []
    source = mi.EODHDIndexSource(api_key="test-key", sleep=sleeps.append)
    return source, wire, sleeps


LISTING = [{"Code": "SAP", "Type": mi.COMMON_STOCK, "Exchange": "XETRA"}]


# --------------------------------------------------------------------------- #
# the listing retries a network failure
# --------------------------------------------------------------------------- #
def test_a_blip_that_clears_is_ridden_out_and_the_listing_is_returned(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _down(), LISTING)
    assert source.common_stocks("XETRA") == LISTING
    assert wire.calls == 2
    assert sleeps == [30.0]


def test_three_tries_over_about_two_minutes_before_giving_up(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _down())
    with pytest.raises(mi.NetworkUnavailable):
        source.common_stocks("XETRA")
    assert wire.calls == 3
    assert sleeps == [30.0, 90.0]
    assert sum(sleeps) == 120.0                      # "about 2 minutes"


def test_the_backoff_is_configurable(monkeypatch):
    wire = _Wire(_down())
    monkeypatch.setattr(mi.urllib.request, "urlopen", wire)
    sleeps: list[float] = []
    source = mi.EODHDIndexSource(api_key="k", sleep=sleeps.append, network_backoff=(1.0,))
    with pytest.raises(mi.NetworkUnavailable):
        source.common_stocks("XETRA")
    assert wire.calls == 2 and sleeps == [1.0]


@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"),
    ConnectionResetError("reset by peer"),
    http.client.IncompleteRead(b"partial"),
    OSError("network is unreachable"),
])
def test_a_timeout_a_reset_and_a_cut_off_body_are_all_network_failures(monkeypatch, failure):
    source, wire, sleeps = _source(monkeypatch, failure)
    with pytest.raises(mi.NetworkUnavailable):
        source.common_stocks("XETRA")
    assert wire.calls == 3 and len(sleeps) == 2


def test_a_server_error_is_a_blip_not_an_answer(monkeypatch):
    """A 503 says nothing about whether the venue exists."""
    source, wire, _sleeps = _source(monkeypatch, _http(503), LISTING)
    assert source.common_stocks("XETRA") == LISTING
    assert wire.calls == 2


def test_the_failure_is_named_in_the_error(monkeypatch):
    source, _wire, _sleeps = _source(monkeypatch, _down())
    with pytest.raises(mi.NetworkUnavailable, match="URLError"):
        source.common_stocks("XETRA")


# --------------------------------------------------------------------------- #
# a 404 is an answer: no retry
# --------------------------------------------------------------------------- #
def test_an_http_404_skips_at_once_with_no_retry_and_no_wait(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _http(404))
    with pytest.raises(mi.ExchangeNotAvailable, match="HTTP 404"):
        source.common_stocks("MI")
    assert wire.calls == 1
    assert sleeps == []


def test_a_404_is_not_a_network_error_and_the_reverse():
    assert not issubclass(mi.ExchangeNotAvailable, mi.NetworkUnavailable)
    assert not issubclass(mi.NetworkUnavailable, mi.ExchangeNotAvailable)
    assert issubclass(mi.ExchangeNotAvailable, mi.MarketIndexError)
    assert issubclass(mi.NetworkUnavailable, mi.MarketIndexError)


def test_another_client_error_is_not_retried_either(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _http(400))
    with pytest.raises(mi.MarketIndexError, match="HTTP 400") as caught:
        source.common_stocks("XETRA")
    assert not isinstance(caught.value, (mi.ExchangeNotAvailable, mi.NetworkUnavailable))
    assert wire.calls == 1 and sleeps == []


# --------------------------------------------------------------------------- #
# what did NOT change
# --------------------------------------------------------------------------- #
def test_a_quota_refusal_is_still_a_stop_and_is_not_retried(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _http(403))
    with pytest.raises(mi.QuotaExhausted):
        source.common_stocks("XETRA")
    assert wire.calls == 1 and sleeps == []


def test_a_rate_limit_still_backs_off_exponentially_and_then_stops(monkeypatch):
    source, wire, sleeps = _source(monkeypatch, _http(429))
    with pytest.raises(mi.QuotaExhausted, match="rate limited five times"):
        source.common_stocks("XETRA")
    assert wire.calls == 5
    assert sleeps == [1, 2, 4, 8, 16]


def test_the_per_symbol_fundamentals_call_fails_fast_without_retrying(monkeypatch):
    """A retry loop here would turn a real outage into a build that never ends: it is counted
    as failed and the row is simply asked about again next build."""
    source, wire, sleeps = _source(monkeypatch, _down())
    with pytest.raises(mi.NetworkUnavailable):
        source.general("SAP.XETRA")
    assert wire.calls == 1 and sleeps == []


def test_requests_and_charges_count_every_attempt(monkeypatch):
    source, _wire, _sleeps = _source(monkeypatch, _down())
    with pytest.raises(mi.NetworkUnavailable):
        source.common_stocks("XETRA")
    assert source.requests == 3
    assert source.charged == 3 * mi.CHARGE_LISTING


# --------------------------------------------------------------------------- #
# the build: the two causes are kept apart
# --------------------------------------------------------------------------- #
class _Refusing:
    """A source that refuses named exchanges with a chosen error, and serves the rest."""

    def __init__(self, refuse: dict) -> None:
        self.refuse = refuse
        self.asked: list[str] = []
        self.requests = self.charged = 0

    def common_stocks(self, exchange):
        self.asked.append(exchange)
        if exchange in self.refuse:
            raise self.refuse[exchange]
        return [{"Code": exchange, "Type": mi.COMMON_STOCK, "Exchange": "XETRA"}]

    def general(self, symbol):
        code = symbol.split(".", 1)[0]
        return {"General": {"Code": code, "Name": f"{code} Inc", "Exchange": "XETRA",
                            "CurrencyCode": "EUR", "PrimaryTicker": symbol,
                            "ISIN": f"DE{code:0>10}", "Industry": "Software"},
                mi.CAP_KEY: 1.0e10}


class _NoUsd:
    def apply(self, row):
        return row


def _build(tmp_path, refuse, exchanges):
    source = _Refusing(refuse)
    outcome = mi.build(exchanges=exchanges, store=mi.IndexStore(tmp_path), source=source,
                       usd=_NoUsd(), today=date(2026, 9, 24), venues={})
    return source, outcome


def test_the_summary_separates_not_available_from_a_network_error(tmp_path):
    _source, outcome = _build(
        tmp_path,
        {"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404"),
         "XETRA": mi.NetworkUnavailable("EODHD /exchange-symbol-list/XETRA: URLError")},
        ["XETRA", "MI", "LSE"])
    assert outcome.skipped_sentence() == (
        "2 exchanges skipped - not available (404): MI; "
        "network error, will retry next build: XETRA (URLError)")
    assert "will retry next build" in outcome.summary()


def test_only_the_network_skips_are_flagged_for_a_rerun(tmp_path):
    _source, outcome = _build(
        tmp_path,
        {"MI": mi.ExchangeNotAvailable("EODHD /x/MI: HTTP 404"),
         "XETRA": mi.NetworkUnavailable("EODHD /x/XETRA: URLError"),
         "LSE": mi.NetworkUnavailable("EODHD /x/LSE: TimeoutError")},
        ["XETRA", "MI", "LSE"])
    assert outcome.network_skipped == ["XETRA", "LSE"]
    assert outcome.skipped_codes(mi.SKIP_NOT_AVAILABLE) == ["MI"]


def test_a_network_skip_does_not_stop_the_exchanges_after_it(tmp_path):
    source, outcome = _build(tmp_path,
                             {"XETRA": mi.NetworkUnavailable("EODHD /x/XETRA: URLError")},
                             ["XETRA", "LSE", "PA"])
    assert source.asked == ["XETRA", "LSE", "PA"]
    assert outcome.fetched == 2 and outcome.stopped == ""


def test_an_unclassified_failure_is_reported_as_failed_not_as_missing(tmp_path):
    _source, outcome = _build(tmp_path, {"XETRA": mi.MarketIndexError("EODHD /x: HTTP 400")},
                              ["XETRA"])
    assert outcome.skipped_sentence() == "1 exchange skipped - failed: XETRA (HTTP 400)"


def test_the_build_log_says_which_kind_it_was(tmp_path):
    _source, _outcome = _build(
        tmp_path,
        {"MI": mi.ExchangeNotAvailable("EODHD /x/MI: HTTP 404"),
         "XETRA": mi.NetworkUnavailable("EODHD /x/XETRA: URLError")}, ["XETRA", "MI"])
    log = mi.build_log_path(mi.IndexStore(tmp_path)).read_text(encoding="utf-8")
    assert "MI: SKIPPED - not available on this plan (HTTP 404)" in log
    assert "XETRA: SKIPPED - network error after retries, will retry next build (URLError)" in log


# --------------------------------------------------------------------------- #
# the incident, end to end
# --------------------------------------------------------------------------- #
def test_the_2026_09_23_blip_no_longer_reads_as_nine_missing_exchanges(monkeypatch, tmp_path):
    """Nine listings, the network down for all of them. Before: nine 'skipped' inside a second,
    indistinguishable from nine 404s. Now: each is retried on the backoff and reported as a
    network error to run again - none of them as 'not available'."""
    wire = _Wire(_down())
    monkeypatch.setattr(mi.urllib.request, "urlopen", wire)
    sleeps: list[float] = []
    source = mi.EODHDIndexSource(api_key="k", sleep=sleeps.append)
    nine = ["US", "TO", "XETRA", "LSE", "PA", "AS", "MC", "SW", "ST"]
    outcome = mi.build(exchanges=nine, store=mi.IndexStore(tmp_path), source=source,
                       usd=_NoUsd(), today=date(2026, 9, 23), venues={})
    assert outcome.network_skipped == nine
    assert outcome.skipped_codes(mi.SKIP_NOT_AVAILABLE) == []
    assert "not available" not in outcome.skipped_sentence()
    assert wire.calls == 9 * 3                       # three tries each, not one
    assert sleeps == [30.0, 90.0] * 9                # two minutes of patience each


def test_a_blip_that_clears_mid_build_costs_nothing(monkeypatch, tmp_path):
    """XETRA answers on the retry: nothing is skipped and the table is built."""
    wire = _Wire(_down(), LISTING, {"General": {"Code": "SAP", "Name": "SAP SE",
                                                "Exchange": "XETRA", "PrimaryTicker": "SAP.XETRA",
                                                "ISIN": "DE0007164600", "Industry": "Software"},
                                    mi.CAP_KEY: 1.0e11})
    monkeypatch.setattr(mi.urllib.request, "urlopen", wire)
    source = mi.EODHDIndexSource(api_key="k", sleep=lambda _s: None)
    outcome = mi.build(exchanges=["XETRA"], store=mi.IndexStore(tmp_path), source=source,
                       usd=_NoUsd(), today=date(2026, 9, 24), venues={})
    assert outcome.skipped_exchanges == []
    assert outcome.fetched == 1


# --------------------------------------------------------------------------- #
# the CLI says what to run
# --------------------------------------------------------------------------- #
def test_the_cli_prints_the_exact_command_for_the_network_skips(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mi, "EODHDIndexSource", lambda *a, **k: _Refusing({
        "MI": mi.ExchangeNotAvailable("EODHD /x/MI: HTTP 404"),
        "XETRA": mi.NetworkUnavailable("EODHD /x/XETRA: URLError")}))
    config = tmp_path / "idx.yaml"
    config.write_text(f"exchanges: [XETRA, MI, LSE]\nroot: {(tmp_path / 'data').as_posix()}\n",
                      encoding="utf-8")
    assert mi.main(["--config", str(config), "build"]) == 0
    printed = capsys.readouterr().out
    assert "NOTE: 2 exchanges skipped - not available (404): MI; network error, will retry " \
           "next build: XETRA (URLError)" in printed
    assert "Run the network-skipped exchanges again with:" in printed
    assert "--exchanges XETRA " in printed
    assert "--exchanges XETRA,MI" not in printed          # the 404 is not worth a rerun


def test_a_build_with_only_404s_prints_no_rerun_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mi, "EODHDIndexSource", lambda *a, **k: _Refusing({
        "MI": mi.ExchangeNotAvailable("EODHD /x/MI: HTTP 404")}))
    config = tmp_path / "idx.yaml"
    config.write_text(f"exchanges: [MI, LSE]\nroot: {(tmp_path / 'data').as_posix()}\n",
                      encoding="utf-8")
    assert mi.main(["--config", str(config), "build"]) == 0
    assert "Run the network-skipped exchanges again" not in capsys.readouterr().out
