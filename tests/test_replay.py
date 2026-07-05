"""Per-run input freezing + offline replay (hardening ITEM 4). A run stores the raw
adapter payloads it consumed; replaying from that frozen record — with an adapter that
CANNOT reach the network — reproduces the verdicts byte-for-byte.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    DataUnavailable,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
)
from aristos_council.pipeline import format_cli_report, run_rank_pipeline
from aristos_council.persistence.replay import (
    FrozenAdapter,
    RecordingAdapter,
    freeze_run,
    make_run_id,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0] * 4, tax_provision=[600.0] * 4,
              pretax_income=[2900.0] * 4, invested_capital=[5000.0] * 4),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0] * 4, tax_provision=[300.0] * 4,
              pretax_income=[1450.0] * 4, invested_capital=[5000.0] * 4),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
              pretax_income=[480.0] * 4, invested_capital=[5000.0] * 4),
}


class _LiveAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1 + (i % 27)), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []

    def get_street_consensus(self, ticker):
        return StreetConsensus(ticker, recommendation_mean=1.9, current_price=100.0)


class _NoNetwork(MarketDataAdapter):
    """A replay must not touch the network — this adapter proves it by blowing up."""
    name = "no-network"

    def get_fundamentals(self, ticker):
        raise AssertionError("network hit during replay (get_fundamentals)")

    def get_price_history(self, ticker, *, start, end):
        raise AssertionError("network hit during replay (get_price_history)")

    def get_dividend_history(self, ticker, *, start, end):
        raise AssertionError("network hit during replay (get_dividend_history)")


# --------------------------------------------------------------------------- #
# Recording + freezing + frozen serving
# --------------------------------------------------------------------------- #
def test_recording_then_frozen_adapter_round_trips(tmp_path):
    rec = RecordingAdapter(_LiveAdapter())
    for t in ("A", "B"):
        rec.get_fundamentals(t)
        rec.get_price_history(t, start=date(2026, 1, 1), end=date(2026, 12, 31))
        rec.get_street_consensus(t)
    run_id = make_run_id("magic_formula_v1")
    freeze_run(rec, run_id=run_id, runs_dir=tmp_path)

    d = tmp_path / run_id
    assert (d / "manifest.json").exists()
    assert (d / "inputs" / "A.json.gz").exists()

    frozen = FrozenAdapter(d)
    assert frozen.name == "fake"                         # provider preserved
    f = frozen.get_fundamentals("A")
    assert f.market_cap == 2e10 and f.pe_ratio == 10.0
    c = frozen.get_street_consensus("A")
    assert c.recommendation_mean == 1.9
    # a ticker not frozen -> DataUnavailable, never a guess
    with pytest.raises(DataUnavailable):
        frozen.get_fundamentals("ZZZZ")


# --------------------------------------------------------------------------- #
# The pre-committed test: replay reproduces the verdicts byte-for-byte, offline
# --------------------------------------------------------------------------- #
def test_replay_reproduces_verdicts_byte_for_byte(tmp_path):
    # 1) a live run that FREEZES its inputs
    live = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_LiveAdapter(), today=date(2026, 6, 30), freeze_dir=tmp_path)
    run_id = live.meta["run_id"]
    assert run_id and (tmp_path / run_id / "manifest.json").exists()

    # 2) replay from the frozen record — the adapter would EXPLODE on any network hit
    replay = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_NoNetwork(), today=date(2026, 6, 30), freeze_dir=tmp_path,
        replay_run_id=run_id)

    # identical verdicts of record — ticker, verdict, combined rank
    assert ([(r.ticker, r.verdict, r.combined_rank) for r in live.ranked]
            == [(r.ticker, r.verdict, r.combined_rank) for r in replay.ranked])
    assert live.excluded == replay.excluded
    # byte-for-byte on the rendered report (the CLI's own output)
    assert format_cli_report(live) == format_cli_report(replay)


def test_replay_needs_no_network_even_for_excluded_and_unrateable(tmp_path):
    live = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_LiveAdapter(), today=date(2026, 6, 30), freeze_dir=tmp_path)
    # C is excluded (ROIC); it must still be replayable from the frozen inputs.
    assert any(t == "C" for t, _ in live.excluded)
    replay = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_v1", ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_NoNetwork(), today=date(2026, 6, 30), freeze_dir=tmp_path,
        replay_run_id=live.meta["run_id"])
    assert replay.excluded == live.excluded
