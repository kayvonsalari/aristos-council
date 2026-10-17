"""SNAPSHOT-2 - the quarterly forward freeze and its 12-month check. Fake adapter, fake grader, no network,
no model, no real cohort data."""
from __future__ import annotations

import csv
import subprocess
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import forward_snapshot as fs
from aristos_council.data.adapter import Fundamentals, PriceBar, PriceHistory
from aristos_council.pipeline import (MultiStrategyCell, MultiStrategyRow, _EXCLUDED, _RANKED,
                                      _TOO_FEW)

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 18)


# --------------------------------------------------------------------------- fakes
def bars(series: dict[date, float]) -> list[PriceBar]:
    return [PriceBar(day=d, open=c, high=c, low=c, close=c, adj_close=c, volume=1)
            for d, c in sorted(series.items())]


class FakeAdapter:
    """prices[ticker] = {day: close}; ccy[ticker] = currency; fx[pair] = {day: rate}."""

    def __init__(self, prices=None, ccy=None, fx=None):
        self.prices, self.ccy, self.fx = prices or {}, ccy or {}, fx or {}
        self.calls: list[str] = []

    def get_price_history(self, ticker, start=None, end=None):
        self.calls.append(ticker)
        src = self.fx.get(ticker) if ticker.endswith("=X") else self.prices.get(ticker)
        if src is None:
            raise KeyError(ticker)
        return PriceHistory(ticker, bars({d: c for d, c in src.items()
                                          if (start is None or d >= start) and (end is None or d <= end)}))

    def get_fundamentals(self, ticker):
        if ticker not in self.ccy:
            raise KeyError(ticker)
        return Fundamentals(ticker=ticker, currency=self.ccy[ticker])


# --------------------------------------------------------------------------- lenses and cohorts
def test_voting_lenses_are_the_visible_equity_selectors_and_nothing_else():
    ids = fs.voting_lens_ids(ROOT / "strategies")
    assert {"magic_formula_momentum_v1", "conservative_plus_v1", "growth_garp_v2", "quality_v1",
            "epv_v1", "magic_formula_raw_v1", "cyclical_income_v1"} <= set(ids)
    assert "forensic_v1" not in ids                                  # a check marks, it does not vote
    assert not [i for i in ids if i.startswith("etf_")]              # fund lenses admit no equity
    assert "growth_garp_v1" not in ids and "magic_formula_v1" not in ids     # ui: hidden


def test_no_overlay_is_a_refusal_not_an_empty_snapshot(tmp_path):
    defs = tmp_path / "defs.yaml"
    defs.write_text(textwrap.dedent("""
        cohorts:
          - name: "Tech - Test"
            industry: ["Semiconductors"]
            exchanges: [US]
            min_market_cap_usd: 1000000000
            min_history_years: 5
            watch: false
    """), encoding="utf-8")
    with pytest.raises(fs.SnapshotError, match="no watched cohorts"):
        fs.watched_definitions(tmp_path, defs)


def _cohort_fixture(tmp_path, tickers=("AAA.US", "BBB.US", "CCC.US")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    defs = tmp_path / "defs.yaml"
    defs.write_text(textwrap.dedent("""
        cohorts:
          - name: "Tech - Test"
            industry: ["Semiconductors"]
            exchanges: [US]
            min_market_cap_usd: 1000000000
            min_history_years: 5
            watch: false
    """), encoding="utf-8")
    root = tmp_path / "cohorts"
    v1 = root / "tech_test" / "v1"
    v1.mkdir(parents=True)
    (root / "watch.yaml").write_text("watch:\n- Tech - Test\n", encoding="utf-8")
    with (v1 / "members.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "yahoo_ticker", "exchange", "industry", "market_cap", "currency", "isin",
                    "name", "source", "filled", "market_cap_usd", "flags"])
        for t in tickers:
            w.writerow([t, t.split(".")[0], "US", "Semiconductors", "5e9", "USD", "", t, "index", "", "5e9", ""])
    return root, defs


def test_cohort_universe_reads_the_current_frozen_version(tmp_path):
    root, _ = _cohort_fixture(tmp_path)
    assert fs.cohort_universe(root, "tech_test") == (1, ["AAA", "BBB", "CCC"], [])
    with pytest.raises(fs.SnapshotError, match="no frozen version"):
        fs.cohort_universe(root, "nope")


# --------------------------------------------------------------------------- USD prices
def test_usd_price_is_close_times_fx_and_pence_are_scaled_first():
    d = date(2026, 10, 16)
    ad = FakeAdapter(
        prices={"US1": {d: 10.0}, "EU1": {d: 20.0}, "UK1": {d: 1500.0}},
        ccy={"US1": "USD", "EU1": "EUR", "UK1": "GBp"},
        fx={"EURUSD=X": {d: 1.1}, "GBPUSD=X": {d: 1.3}})
    cache: dict = {}
    assert fs.latest_quote(ad, "US1", TODAY, cache).price_usd == 10.0
    assert fs.latest_quote(ad, "EU1", TODAY, cache).price_usd == pytest.approx(22.0)
    uk = fs.latest_quote(ad, "UK1", TODAY, cache)
    assert (uk.price_local, uk.currency) == (15.0, "GBP")             # 1500p is 15 pounds
    assert uk.price_usd == pytest.approx(19.5)


def test_a_price_that_cannot_be_converted_is_empty_with_a_reason_never_a_guess():
    d = date(2026, 10, 16)
    ad = FakeAdapter(prices={"X": {d: 5.0}, "Y": {d: 5.0}, "Z": {d: 5.0}}, ccy={"X": "", "Y": "SEK"})
    assert fs.latest_quote(ad, "X", TODAY, {}).price_usd is None
    assert "currency unknown" in fs.latest_quote(ad, "X", TODAY, {}).note
    assert "SEK->USD" in fs.latest_quote(ad, "Y", TODAY, {}).note        # no SEKUSD=X series
    assert fs.latest_quote(ad, "Z", TODAY, {}).note.startswith("currency unknown")     # fundamentals missing
    assert fs.latest_quote(FakeAdapter(), "Q", TODAY, {}).price_usd is None


# --------------------------------------------------------------------------- rows
def _cell(sid, status=_RANKED, position=1, size=10, verdict="buy", reason=""):
    return MultiStrategyCell(strategy_id=sid, status=status, position=position, cohort_size=size,
                             verdict=verdict, reason=reason)


def _multi():
    ids = ["lens_a", "lens_b", "lens_c"]
    rows = [
        MultiStrategyRow("AAA", "AAA", {"lens_a": _cell("lens_a", position=1, verdict="buy"),
                                        "lens_b": _cell("lens_b", position=2, verdict="buy"),
                                        "lens_c": _cell("lens_c", _EXCLUDED, None, 0, "", "ROIC below 12%")}),
        MultiStrategyRow("BBB", "BBB", {"lens_a": _cell("lens_a", position=5, verdict="hold"),
                                        "lens_b": _cell("lens_b", position=9, verdict="sell"),
                                        "lens_c": _cell("lens_c", _TOO_FEW, None, 3, "")}),
    ]
    return SimpleNamespace(rows=rows, strategy_ids=ids)


def test_rows_carry_the_frozen_fields_and_an_exclusion_has_no_verdict():
    q = {"AAA": fs.Quote(10.0, "USD", 1.0, date(2026, 10, 16)),
         "BBB": fs.Quote(note="currency unknown")}
    rows = fs.build_rows(TODAY, "Tech - Test", "tech_test", 3, _multi(), q,
                         lambda slug, sid: "proven here" if sid == "lens_a" else "untested here",
                         ["lens_a", "lens_b", "lens_c"])
    assert len(rows) == 6 and list(rows[0]) == list(fs.COLUMNS)
    a_c = next(r for r in rows if r["ticker"] == "AAA" and r["lens"] == "lens_c")
    assert (a_c["status"], a_c["verdict"], a_c["rank"]) == ("excluded", "", "")        # null is not a HOLD
    assert "ROIC" in a_c["reason"]
    a_a = next(r for r in rows if r["ticker"] == "AAA" and r["lens"] == "lens_a")
    assert (a_a["date"], a_a["cohort"], a_a["cohort_version"], a_a["rank"], a_a["ranked_of"], a_a["verdict"],
            a_a["badge"], a_a["price_usd"], a_a["price_date"]) == (
        "2026-10-18", "Tech - Test", 3, 1, 10, "BUY", "proven here", "10", "2026-10-16")
    # the lens that excluded AAA did not vote: 2 BUYs out of 2 voters, not out of 3 lenses
    assert (a_a["agreement_buy"], a_a["agreement_voted"]) == (2, 2)
    b = next(r for r in rows if r["ticker"] == "BBB" and r["lens"] == "lens_c")
    assert (b["status"], b["verdict"]) == ("too_few", "")
    assert (b["agreement_buy"], b["agreement_voted"]) == (0, 2)
    assert b["price_usd"] == "" and "currency unknown" in b["reason"]


def test_a_snapshot_is_never_overwritten(tmp_path):
    p = fs.write_snapshot([{c: "" for c in fs.COLUMNS}], tmp_path, TODAY)
    assert p.name == "forward_2026-10-18.csv"
    with pytest.raises(fs.SnapshotError, match="never overwritten"):
        fs.write_snapshot([], tmp_path, TODAY)
    assert fs.read_snapshot(p)[0].keys() == set(fs.COLUMNS)


# --------------------------------------------------------------------------- take
def _fake_grade(calls, errors=()):
    def grade(universe, lenses, **kw):
        calls.append(dict(kw, universe=list(universe), lenses=list(lenses)))
        rows = [MultiStrategyRow(t, t, {s: _cell(s, position=i + 1, verdict=("buy", "hold", "sell")[i % 3])
                                        for s in lenses}) for i, t in enumerate(universe)]
        results = {s: SimpleNamespace(fetch_errors=list(errors)) for s in lenses}
        return SimpleNamespace(rows=rows, results=results, strategy_ids=list(lenses))
    return grade


@pytest.fixture
def fake_agreement(monkeypatch):
    from aristos_council import pipeline
    monkeypatch.setattr(pipeline, "lens_agreement",
                        lambda multi: SimpleNamespace(voting_ids=list(multi.strategy_ids)))


def _take(tmp_path, **kw):
    root, defs = _cohort_fixture(tmp_path)
    d = date(2026, 10, 16)
    ad = FakeAdapter(prices={t: {d: 10.0 + i} for i, t in enumerate(("AAA", "BBB", "CCC"))},
                     ccy={t: "USD" for t in ("AAA", "BBB", "CCC")})
    calls: list = []
    gitlog: list = []
    kw.setdefault("grade", _fake_grade(calls))
    res = fs.take_snapshot(today=TODAY, adapter=ad, out_dir=tmp_path / "snaps", cohorts_root=root,
                           definitions_path=defs, strategies_dir=ROOT / "strategies",
                           track_record=lambda slug, sid: "no edge shown here",
                           git=lambda *a: gitlog.append(a), **kw)
    return res, calls, gitlog


def test_take_runs_every_voting_lens_ranker_only_and_writes_one_row_per_name_and_lens(tmp_path, fake_agreement):
    res, calls, _ = _take(tmp_path)
    assert calls and calls[0]["ranker_only"] is True                      # no model, no runners
    assert calls[0]["lenses"] == fs.voting_lens_ids(ROOT / "strategies")
    assert calls[0]["universe"] == ["AAA", "BBB", "CCC"]
    assert res.cohorts == 1 and res.names == 3 and res.rows == 3 * len(res.lenses)
    rows = fs.read_snapshot(res.path)
    assert {r["cohort"] for r in rows} == {"Tech - Test"} and {r["cohort_version"] for r in rows} == {"1"}
    assert {r["badge"] for r in rows} == {"no edge shown here"}
    assert next(r for r in rows if r["ticker"] == "AAA")["price_usd"] == "10"


def test_take_commits_only_the_snapshot_file_with_a_dated_message(tmp_path, fake_agreement):
    res, _, git = _take(tmp_path, commit=True)
    assert res.committed
    assert git[0] == ("add", "--", str(res.path))
    assert git[1] == ("commit", "-m", "forward snapshot 2026-10-18", "--", str(res.path))
    _, _, none = _take(tmp_path / "x", commit=False)
    assert none == []


def test_a_fetch_error_stops_the_freeze_unless_allowed(tmp_path, fake_agreement):
    with pytest.raises(fs.SnapshotError, match="transient fetch failure.*Tech - Test: AAA"):
        _take(tmp_path, grade=_fake_grade([], errors=[("AAA", "timeout")]))
    assert not (tmp_path / "snaps").exists()
    res, _, _ = _take(tmp_path / "ok", grade=_fake_grade([], errors=[("AAA", "timeout")]),
                      allow_fetch_errors=True)
    assert res.fetch_errors == [("Tech - Test", "AAA", "timeout")]


def test_take_refuses_to_overwrite_before_spending_anything(tmp_path, fake_agreement):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    (snaps / "forward_2026-10-18.csv").write_text("x", encoding="utf-8")
    calls: list = []
    with pytest.raises(fs.SnapshotError, match="never overwritten"):
        _take(tmp_path, grade=_fake_grade(calls))
    assert calls == []


# --------------------------------------------------------------------------- check
def _row(cohort, lens, ticker, verdict, price=10.0, fx="1", ccy="USD", buy=0, voted=3, badge="proven here"):
    return {"date": "2025-10-01", "cohort": cohort, "cohort_version": "1", "lens": lens, "ticker": ticker,
            "status": "ranked" if verdict else "excluded", "rank": "1", "ranked_of": "9", "verdict": verdict,
            "badge": badge, "agreement_buy": str(buy), "agreement_voted": str(voted),
            "price_local": str(price), "currency": ccy, "fx_to_usd": fx, "price_usd": str(price),
            "price_date": "2025-09-30", "reason": ""}


SNAP = date(2025, 10, 1)
THEN, NOW = date(2025, 10, 1), date(2026, 10, 16)


def _universe(moves: dict[str, float]):
    return FakeAdapter(prices={t: {THEN: 10.0, NOW: 10.0 * (1 + m)} for t, m in moves.items()},
                       ccy={t: "USD" for t in moves})


def test_buys_are_compared_with_holds_and_sells_equal_weight_in_the_same_cohort():
    moves = {"B1": 0.30, "B2": 0.10, "B3": 0.20, "H1": 0.05, "H2": 0.00, "H3": 0.10,
             "S1": -0.10, "S2": 0.00, "S3": 0.10}
    rows = [_row("C", "lens_a", t, "BUY" if t[0] == "B" else "HOLD" if t[0] == "H" else "SELL") for t in moves]
    cells, left = fs.grade_snapshot(rows, SNAP, _universe(moves), date(2026, 10, 18))
    c = cells[0]
    assert (c.buy.n, c.hold.n, c.sell.n) == (3, 3, 3) and not left
    assert c.buy.mean == pytest.approx(0.20) and c.hold.mean == pytest.approx(0.05)
    assert c.sell.mean == pytest.approx(0.0) and c.everyone.mean == pytest.approx(0.0833333, abs=1e-6)
    assert c.beat_hold and c.beat_sell and c.beat_both
    assert fs._verdict_words(c) == "BUY beat HOLD and SELL"


def test_cohorts_and_lenses_are_not_pooled():
    moves = {f"{k}{i}": m for k, ms in (("B", (0.3, 0.2, 0.1)), ("H", (0.0, 0.0, 0.0)), ("S", (-.1, -.1, -.1)))
             for i, m in enumerate(ms)}
    rows = [_row(c, l, t, "BUY" if t[0] == "B" else "HOLD" if t[0] == "H" else "SELL")
            for c in ("C1", "C2") for l in ("lens_a", "lens_b") for t in moves]
    cells, _ = fs.grade_snapshot(rows, SNAP, _universe(moves), date(2026, 10, 18))
    assert [(c.cohort, c.lens) for c in cells] == [("C1", "lens_a"), ("C1", "lens_b"),
                                                   ("C2", "lens_a"), ("C2", "lens_b")]


def test_a_group_under_three_names_states_no_mean_and_no_winner():
    moves = {"B1": 0.5, "B2": 0.5, "H1": 0.0, "H2": 0.0, "H3": 0.0, "S1": 0.0, "S2": 0.0, "S3": 0.0}
    rows = [_row("C", "l", t, "BUY" if t[0] == "B" else "HOLD" if t[0] == "H" else "SELL") for t in moves]
    c = fs.grade_snapshot(rows, SNAP, _universe(moves), date(2026, 10, 18))[0][0]
    assert c.buy.mean is None and c.beat_both is None
    assert fs._verdict_words(c) == "too few names to compare"


def test_excluded_names_are_not_graded_and_a_vanished_name_is_left_out_not_scored_as_a_loss():
    moves = {"B1": 0.3, "B2": 0.2, "B3": 0.1, "H1": 0, "H2": 0, "H3": 0, "S1": 0, "S2": 0, "S3": 0}
    rows = [_row("C", "l", t, "BUY" if t[0] == "B" else "HOLD" if t[0] == "H" else "SELL") for t in moves]
    rows.append(_row("C", "l", "X1", ""))                           # excluded: no verdict, no return
    rows.append(_row("C", "l", "GONE", "BUY"))                      # delisted: no bars at all
    ad = _universe(moves)
    ad.prices["GONE"], ad.ccy["GONE"] = {THEN: 10.0}, "USD"         # last bar a year old
    c, left = fs.grade_snapshot(rows, SNAP, ad, date(2026, 10, 18))
    assert left == {"GONE": "no current price"} and c[0].no_return == 1
    assert c[0].buy.n == 3 and c[0].buy.mean == pytest.approx(0.2)  # GONE did not drag it down


def test_a_split_cannot_fake_a_return_because_both_ends_come_from_one_series():
    # frozen at 100; the provider has since restated the history for a 2:1 split -> 50 then, 55 now
    ad = FakeAdapter(prices={"S": {THEN: 50.0, NOW: 55.0}}, ccy={"S": "USD"})
    r = fs.usd_return(ad, "S", SNAP, 1.0, date(2026, 10, 18), {}, "USD")
    assert r.usd_return == pytest.approx(0.10)


def test_the_return_is_in_usd_so_the_fx_move_counts():
    ad = FakeAdapter(prices={"E": {THEN: 20.0, NOW: 22.0}}, ccy={"E": "EUR"},
                     fx={"EURUSD=X": {NOW: 1.21}})
    r = fs.usd_return(ad, "E", SNAP, 1.10, date(2026, 10, 18), {}, "EUR")      # +10% local, +10% euro
    assert r.usd_return == pytest.approx(1.10 * 1.10 - 1)
    assert fs.usd_return(ad, "E", SNAP, None, date(2026, 10, 18), {}, "EUR").usd_return is None


def test_check_says_when_a_snapshot_is_too_young_and_when_it_is_due(tmp_path):
    for d, ok in ((date(2026, 7, 5), True), (date(2025, 10, 1), False)):
        row = _row("C", "l", "B1", "BUY")
        fs.write_snapshot([dict(row, date=d.isoformat())], tmp_path, d)
    (tmp_path / "forward_notadate.csv").write_text("x")                 # ignored, never a crash
    (tmp_path / "verdict_consensus.csv").write_text("x")                # the old July file is not one of these
    said: list[str] = []
    ad = _universe({"B1": 0.1})
    n = fs.check_snapshots(tmp_path, today=date(2026, 10, 18), adapter=ad, say=said.append)
    text = "\n".join(said)
    assert n == 1
    assert "forward_2026-07-05.csv: 105 days old - not graded until it is 365 (2027-07-05)" in text
    assert "Snapshot 2025-10-01 checked on 2026-10-18" in text
    assert fs.check_snapshots(tmp_path / "empty", today=TODAY, adapter=ad, say=said.append) == 0
    assert "No forward snapshots" in said[-1]


def test_the_report_gives_the_count_of_cells_not_a_single_winner():
    moves = {"B1": 0.3, "B2": 0.2, "B3": 0.1, "H1": 0, "H2": 0, "H3": 0, "S1": -.1, "S2": -.1, "S3": -.1}
    rows = [_row("C", "l", t, "BUY" if t[0] == "B" else "HOLD" if t[0] == "H" else "SELL", buy=1 if t[0] == "B" else 0)
            for t in moves]
    ad = _universe(moves)
    cells, left = fs.grade_snapshot(rows, SNAP, ad, date(2026, 10, 18))
    text = fs.format_check(SNAP, date(2026, 10, 18), cells, left,
                           fs.agreement_table(rows, SNAP, ad, date(2026, 10, 18)))
    assert "BUY beat both HOLD and SELL in 1 of 1 comparable cohort x lens cells" in text
    assert "dividends not included" in text and "chance" in text
    assert "1 BUY:" in text and "0 BUY:" in text


# --------------------------------------------------------------------------- no model
def test_the_module_imports_no_model():
    probe = textwrap.dedent("""
        import sys
        import aristos_council.forward_snapshot as m
        bad = sorted(r for r in ("langchain", "langchain_core", "langchain_anthropic", "langgraph", "anthropic",
                                 "openai") if r in sys.modules)
        print(",".join(bad))
    """)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"imports a model library: {out.stdout.strip()}"
