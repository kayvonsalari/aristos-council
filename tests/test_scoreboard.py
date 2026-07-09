"""Prospective scoreboard — snapshot freezing + forward-return scoring.

Deterministic and network-free (adapter seam). Covers: correct rows appended, nulls
preserved, append-only (never rewritten), EXCLUDED/UNRATEABLE recorded, the SELL
relative-rank note, the sticky-label flag; and for scoring: known bucket means,
deterministic tercile edges, UNRESOLVED (stopped trading, never -100%), the
horizon-not-elapsed partial label, and empty-street names still scored by Aristos.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    DataUnavailable,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
)
from aristos_council.pipeline import RankPipelineResult
from aristos_council.rank_engine import RankedTicker
from aristos_council.scoreboard import (
    build_snapshot_rows,
    compute_return,
    divergence_map,
    format_strategy_score,
    read_rows,
    run_snapshot,
    score_snapshot,
    street_terciles,
    SnapshotRow,
    append_rows,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


# --------------------------------------------------------------------------- #
# ITEM 5 — whole-row-quoting bug: writer never whole-row-quotes; commas in notes
# round-trip; legacy whole-row-quoted rows are repaired on read.
# --------------------------------------------------------------------------- #
_COMMA_NOTE = "bottom quintile of 23-name universe (relative rank, not a short thesis)"


def test_comma_in_notes_round_trips_and_is_not_whole_row_quoted(tmp_path):
    import csv as _csv

    row = SnapshotRow(
        snapshot_date="2026-07-05", strategy="magic_formula_momentum_v1", universe_id="",
        ticker="GE", aristos_verdict="SELL", combined_rank=43.0, price=377.52,
        street_mean=1.5, n_analysts=21, target_mean=362.57144, notes=_COMMA_NOTE)
    path = tmp_path / "verdict_consensus.csv"
    append_rows([row], path)

    # a strict CSV parse yields the full field row (NOT a single whole-row-quoted column)
    lines = path.read_text(encoding="utf-8").splitlines()
    header_fields = next(_csv.reader([lines[0]]))
    data_fields = next(_csv.reader([lines[1]]))
    assert not lines[1].startswith('"')                  # row is never whole-row-quoted
    assert len(data_fields) == len(header_fields) > 1    # real fields, not one column
    # and the value round-trips intact (comma preserved)
    back = read_rows(path)[0]
    assert back.ticker == "GE" and back.notes == _COMMA_NOTE and back.combined_rank == 43.0


def test_legacy_whole_row_quoted_line_is_repaired_on_read(tmp_path):
    # a row written the OLD (buggy) way: the entire row as one quoted field.
    header = ("snapshot_date,strategy,universe_id,ticker,aristos_verdict,combined_rank,"
              "price,street_mean,n_analysts,target_mean,notes")
    inner = f'2026-07-05,magic_formula_momentum_v1,,GE,SELL,43.0,377.52,1.5,21,362.57144,"{_COMMA_NOTE}"'
    quoted = '"' + inner.replace('"', '""') + '"'
    path = tmp_path / "legacy.csv"
    path.write_text(header + "\n" + quoted + "\n", encoding="utf-8")

    rows = read_rows(path)
    assert len(rows) == 1
    assert rows[0].ticker == "GE" and rows[0].aristos_verdict == "SELL"
    assert rows[0].combined_rank == 43.0 and rows[0].notes == _COMMA_NOTE


# --------------------------------------------------------------------------- #
# Fake adapter for the ranker-only pipeline + street consensus
# --------------------------------------------------------------------------- #
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4),
}

_CONSENSUS = {
    "A": StreetConsensus("A", recommendation_mean=1.5, n_analysts=20,
                         target_mean_price=120.0, current_price=100.0),
    # B: partial data — n_analysts + target null (abstain-not-guess must be preserved)
    "B": StreetConsensus("B", recommendation_mean=2.2, n_analysts=None,
                         target_mean_price=None, current_price=90.0),
}


class _SnapAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker == "DEAD":
            return Fundamentals(ticker="DEAD")             # delisted shell
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        if ticker == "DEAD":
            raise RuntimeError("delisted")
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []

    def get_street_consensus(self, ticker):
        return _CONSENSUS.get(ticker, StreetConsensus(ticker=ticker))


# --------------------------------------------------------------------------- #
# Snapshot job
# --------------------------------------------------------------------------- #
def test_run_snapshot_appends_rows_with_nulls_excluded_and_unrateable(tmp_path):
    rows, path = run_snapshot(
        ["A", "B", "C", "DEAD"], "magic_formula_v1", adapter=_SnapAdapter(),
        today=date(2026, 6, 30), strategies_dir=STRAT_DIR, out_dir=tmp_path)

    by = {r.ticker: r for r in rows}
    assert by["A"].aristos_verdict == "BUY" and by["A"].price == 100.0
    assert by["A"].street_mean == 1.5 and by["A"].n_analysts == 20
    # C failed the quality-value prefilter (ROIC) -> EXCLUDED:<criterion>
    assert by["C"].aristos_verdict.startswith("EXCLUDED:") and "min_roic" in by["C"].aristos_verdict
    # DEAD had no data at all -> UNRATEABLE (a call too, recorded)
    assert by["DEAD"].aristos_verdict == "UNRATEABLE"

    # nulls PRESERVED through the CSV round-trip (abstain-not-guess on analyst data)
    reloaded = {r.ticker: r for r in read_rows(path)}
    assert reloaded["B"].n_analysts is None and reloaded["B"].target_mean is None
    assert reloaded["B"].street_mean == 2.2

    # APPEND-ONLY: a second run adds rows, never rewrites.
    run_snapshot(["A", "B", "C", "DEAD"], "magic_formula_v1", adapter=_SnapAdapter(),
                 today=date(2026, 6, 30), strategies_dir=STRAT_DIR, out_dir=tmp_path)
    assert len(read_rows(path)) == 2 * len(rows)


def test_sell_row_carries_the_relative_rank_note():
    ranked = [
        RankedTicker(ticker=t, factor_ranks={"f": float(i)}, factor_values={},
                     combined_rank=float(i), universe_size=5, verdict=v)
        for i, (t, v) in enumerate(
            [("A", "buy"), ("B", "hold"), ("C", "hold"), ("D", "hold"), ("E", "sell")], 1)
    ]
    result = RankPipelineResult(
        ranked=ranked, excluded=[], unrateable=[], narratives={}, header="",
        meta={"rank_strategy_id": "magic_formula_v1"})
    rows = build_snapshot_rows(result, {}, snapshot_date=date(2026, 7, 4),
                               strategy="magic_formula_v1")
    sell = next(r for r in rows if r.ticker == "E")
    assert sell.aristos_verdict == "SELL"
    assert sell.notes == ("bottom quintile of 5-name universe "
                          "(relative rank, not a short thesis)")
    assert next(r for r in rows if r.ticker == "A").notes == ""    # BUY has no note


# --------------------------------------------------------------------------- #
# Street terciles + divergence map
# --------------------------------------------------------------------------- #
def _srow(ticker, verdict, street_mean, *, target=None, price=None):
    return SnapshotRow(snapshot_date="2026-07-04", strategy="s", universe_id="u",
                       ticker=ticker, aristos_verdict=verdict, combined_rank=None,
                       price=price, street_mean=street_mean, n_analysts=None,
                       target_mean=target, notes="")


def test_street_terciles_edge_ties_go_to_the_more_loved_bucket():
    # six names; the loved/middle boundary lands inside a tie at 1.0 -> all three 1.0s
    # go LOVED (more-loved), deterministically.
    rows = [_srow(f"T{i}", "HOLD", m) for i, m in
            enumerate([1.0, 1.0, 1.0, 2.0, 3.0, 4.0])]
    t = street_terciles(rows)
    assert t["T0"] == t["T1"] == t["T2"] == "most-loved"
    assert t["T3"] == "middle"
    assert t["T4"] == "least-loved" and t["T5"] == "least-loved"


def test_null_street_names_are_omitted_from_terciles():
    rows = [_srow("A", "BUY", 1.2), _srow("B", "HOLD", 2.0), _srow("X", "BUY", None)]
    assert "X" not in street_terciles(rows)


def test_sticky_label_and_tercile_disagreement_flags():
    rows = [
        _srow("LOVE", "SELL", 1.1, target=90.0, price=100.0),   # loved rating, target<=price
        _srow("MID", "HOLD", 2.0),
        _srow("HATE", "BUY", 3.0),                              # unloved rating, aristos BUY
    ]
    dm = {d.ticker: d for d in divergence_map(rows)}
    assert dm["LOVE"].sticky_label is True                      # top tercile + target<=price
    assert dm["LOVE"].tercile_disagreement is True             # aristos SELL vs street loved
    assert dm["HATE"].tercile_disagreement is True             # aristos BUY vs street unloved
    assert dm["MID"].sticky_label is False


def test_sticky_label_does_not_fire_when_target_above_price():
    rows = [_srow("A", "BUY", 1.2, target=130.0, price=100.0),
            _srow("B", "HOLD", 2.0), _srow("C", "SELL", 3.0)]
    assert divergence_map(rows)[0].sticky_label is False        # target > price -> not sticky


# --------------------------------------------------------------------------- #
# Forward-return scoring
# --------------------------------------------------------------------------- #
def _bars(pairs):
    return [PriceBar(day=d, open=p, high=p, low=p, close=p, adj_close=p, volume=1)
            for d, p in pairs]


class _PriceAdapter(MarketDataAdapter):
    name = "fake"

    def __init__(self, bars_by_ticker):
        self._b = bars_by_ticker

    def get_fundamentals(self, ticker):
        raise NotImplementedError

    def get_dividend_history(self, ticker, *, start, end):
        return []

    def get_price_history(self, ticker, *, start, end):
        bars = self._b.get(ticker)
        if bars is None:
            raise DataUnavailable(f"no history for {ticker}")
        return PriceHistory(ticker=ticker, bars=bars)


def test_compute_return_uses_adjusted_closes_over_the_window():
    bars = _bars([(date(2026, 1, 5), 100.0), (date(2026, 7, 5), 150.0),
                  (date(2026, 7, 10), 155.0)])
    r = compute_return(bars, date(2026, 1, 5), date(2026, 7, 5))
    assert r.status == "OK" and abs(r.ret - 0.5) < 1e-9        # 100 -> 150 = +50%


def test_compute_return_unresolved_when_stopped_trading():
    bars = _bars([(date(2026, 1, 5), 100.0), (date(2026, 2, 1), 90.0)])   # delisted Feb
    r = compute_return(bars, date(2026, 1, 5), date(2026, 7, 5))
    assert r.status == "UNRESOLVED" and r.ret is None
    assert "not assumed -100%" in r.note                       # acquisition-safe wording


def test_score_snapshot_bucket_means_and_ordering_fully_elapsed():
    snap = date(2026, 1, 5)
    want_end = date(2026, 7, 5)
    today = date(2026, 7, 10)
    prices = {
        "WIN": _bars([(snap, 100.0), (want_end, 150.0), (today, 152.0)]),    # +50%
        "MID": _bars([(snap, 100.0), (want_end, 110.0), (today, 111.0)]),    # +10%
        "LOSE": _bars([(snap, 100.0), (want_end, 80.0), (today, 79.0)]),     # -20%
        "NOSTREET": _bars([(snap, 100.0), (want_end, 120.0), (today, 121.0)]),  # +20%
        "GONE": _bars([(snap, 100.0), (date(2026, 2, 1), 90.0)]),            # delisted
    }
    rows = [
        _srow("WIN", "BUY", 1.2), _srow("MID", "HOLD", 2.0), _srow("LOSE", "SELL", 3.0),
        _srow("NOSTREET", "BUY", None),                        # empty street data
        SnapshotRow("2026-01-05", "s", "u", "GONE", "EXCLUDED:min_roic", None, None,
                    None, None, None, "screen"),
    ]
    scores, partial = score_snapshot(rows, adapter=_PriceAdapter(prices),
                                     snapshot_date=snap, today=today, horizon_months=6)
    s = scores["s"]
    assert partial is False
    aristos = {b.bucket: b for b in s.aristos}
    assert aristos["BUY"].n == 2                               # WIN + NOSTREET
    assert abs(aristos["BUY"].mean - 0.35) < 1e-9             # (0.50 + 0.20)/2
    assert abs(aristos["SELL"].mean + 0.20) < 1e-9
    assert s.aristos_ordered is True                          # BUY > HOLD > SELL
    street = {b.bucket: b for b in s.street}
    assert street["most-loved"].n == 1                        # NOSTREET excluded from terciles
    assert s.street_ordered is True
    assert abs(s.universe_mean - 0.15) < 1e-9                 # (50+10-20+20)/4
    assert any(t == "GONE" for t, _ in s.unresolved)          # reported, not dropped


def test_score_snapshot_partial_period_is_labelled_not_silent():
    snap = date(2026, 6, 1)
    today = date(2026, 7, 10)
    prices = {"X": _bars([(snap, 100.0), (today, 130.0)])}
    rows = [_srow("X", "BUY", 1.2)]
    scores, partial = score_snapshot(rows, adapter=_PriceAdapter(prices),
                                     snapshot_date=snap, today=today, horizon_months=12)
    assert partial is True                                    # 12mo horizon not elapsed
    text = format_strategy_score(scores["s"], snapshot_date=snap,
                                 horizon_months=12, partial=partial)
    assert "partial period" in text
    assert "One snapshot is an anecdote with arithmetic" in text   # standing caveat verbatim


# --------------------------------------------------------------------------- #
# Adapter seam — consensus default + delegation
# --------------------------------------------------------------------------- #
class _BareAdapter(MarketDataAdapter):
    name = "bare"

    def get_fundamentals(self, ticker):
        raise NotImplementedError

    def get_price_history(self, ticker, *, start, end):
        raise NotImplementedError

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_default_consensus_is_all_null_abstention():
    c = _BareAdapter().get_street_consensus("ZZZ")
    assert c.ticker == "ZZZ"
    assert (c.recommendation_mean, c.n_analysts, c.target_mean_price,
            c.current_price) == (None, None, None, None)


def test_caching_adapter_delegates_consensus(tmp_path):
    from aristos_council.data.cache import CachingAdapter

    class _Inner(_BareAdapter):
        def get_street_consensus(self, ticker):
            return StreetConsensus(ticker, recommendation_mean=1.8, n_analysts=12,
                                   target_mean_price=200.0, current_price=180.0)

    cad = CachingAdapter(_Inner(), cache_dir=tmp_path, today=date(2026, 7, 4))
    c = cad.get_street_consensus("MSFT")
    assert c.recommendation_mean == 1.8 and c.n_analysts == 12
    # served from cache on the second call (round-trips through JSON unchanged)
    assert cad.get_street_consensus("MSFT").target_mean_price == 200.0
