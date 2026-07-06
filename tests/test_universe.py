"""Universe manifests — a declared, versioned input recorded on every rank run (Item 1).

Manifest load/validate, unknown-id error, ad-hoc fingerprinting, and the plumbing that
stamps the universe id into run meta and the snapshot CSV rows. Network-free.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
)
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.scoreboard import append_rows, read_rows, run_snapshot
from aristos_council.universe import (
    adhoc_universe_id,
    load_universe,
    load_universe_by_id,
    list_universes,
)

REPO = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = REPO / "universes"
STRAT_DIR = REPO / "strategies"

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

    def get_street_consensus(self, ticker):
        return StreetConsensus(ticker, recommendation_mean=1.8, current_price=100.0)


def _write_manifest(dir_: Path, tickers: list[str], *, uid="test_uni_v1") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{uid}.yaml").write_text(
        f"id: {uid}\ndescription: test\ncreated: '2026-07-05'\nrationale: test\n"
        "tickers:\n" + "".join(f"  - {t}\n" for t in tickers), encoding="utf-8")
    return dir_


# --------------------------------------------------------------------------- #
# Manifest load / validate
# --------------------------------------------------------------------------- #
def test_growth_40_manifest_loads_with_40_names():
    u = load_universe_by_id("growth_40_v1", UNIVERSES_DIR)
    assert u.id == "growth_40_v1"
    assert len(u.tickers) == 40
    assert "NVDA" in u.tickers and "XOM" in u.tickers


def test_defensive_16_manifest_is_the_validated_set():
    u = load_universe_by_id("defensive_16_v1", UNIVERSES_DIR)
    assert u.id == "defensive_16_v1"
    assert len(u.tickers) == 16
    # the EXACT validated set (staples + healthcare + T/VZ/MMM trap controls);
    # utilities and the never-validated names are absent.
    assert u.tickers == ["PG", "KO", "CL", "KMB", "PEP", "MCD", "WMT", "MDLZ", "GIS",
                         "HSY", "JNJ", "ABT", "MRK", "T", "VZ", "MMM"]
    assert not ({"DUK", "SO", "NEE", "MO", "PFE", "ABBV"} & set(u.tickers))


def test_unknown_universe_id_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown universe id 'nope_v9'"):
        load_universe_by_id("nope_v9", UNIVERSES_DIR)


def test_manifest_normalizes_and_dedupes_tickers(tmp_path):
    _write_manifest(tmp_path, ["aapl", "MSFT", "aapl", "brk.b"])
    u = load_universe(tmp_path / "test_uni_v1.yaml")
    assert u.tickers == ["AAPL", "MSFT", "BRK.B"]        # upper, de-duped, order kept


def test_manifest_id_must_encode_a_version(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "id: growth_no_version\ntickers: [AAPL]\n", encoding="utf-8")
    with pytest.raises(Exception, match="must encode a version"):
        load_universe(tmp_path / "bad.yaml")


def test_list_universes_includes_growth_40():
    assert "growth_40_v1" in {u.id for u in list_universes(UNIVERSES_DIR)}


# --------------------------------------------------------------------------- #
# Ad-hoc fingerprint
# --------------------------------------------------------------------------- #
def test_adhoc_id_is_stable_and_order_insensitive():
    a = adhoc_universe_id(["AAPL", "MSFT", "GOOGL"])
    b = adhoc_universe_id(["googl", "aapl", "msft"])        # different order/case
    assert a == b and a.startswith("adhoc:") and len(a) == len("adhoc:") + 8
    assert adhoc_universe_id(["AAPL", "TSLA"]) != a         # different set -> different id


# --------------------------------------------------------------------------- #
# Pipeline records the id (meta) — manifest vs ad-hoc
# --------------------------------------------------------------------------- #
def test_pipeline_records_manifest_universe_id(tmp_path):
    _write_manifest(tmp_path, ["A", "B", "C"])
    result = run_rank_pipeline(
        None, "magic_formula_v1", universe_id="test_uni_v1", universes_dir=tmp_path,
        ranker_only=True, strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=date(2026, 6, 30))
    assert result.meta["universe_id"] == "test_uni_v1"
    assert {r.ticker for r in result.ranked} <= {"A", "B", "C"}


def test_pipeline_records_adhoc_id_for_explicit_list():
    result = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    assert result.meta["universe_id"].startswith("adhoc:")


def test_pipeline_needs_a_universe_or_an_id():
    with pytest.raises(ValueError, match="needs an explicit"):
        run_rank_pipeline(None, "magic_formula_v1", ranker_only=True,
                          strategies_dir=STRAT_DIR, adapter=_Adapter())


# --------------------------------------------------------------------------- #
# Snapshot rows carry the id
# --------------------------------------------------------------------------- #
def test_snapshot_rows_carry_the_manifest_id(tmp_path):
    manifests = tmp_path / "universes"
    _write_manifest(manifests, ["A", "B", "C"])
    rows, _ = run_snapshot(None, "magic_formula_v1", adapter=_Adapter(),
                           today=date(2026, 6, 30), strategies_dir=STRAT_DIR,
                           out_dir=tmp_path, universe_id="test_uni_v1",
                           universes_dir=manifests)
    assert rows and all(r.universe_id == "test_uni_v1" for r in rows)


# --------------------------------------------------------------------------- #
# CSV schema upgrade — append to a pre-universe_id file stays aligned
# --------------------------------------------------------------------------- #
def test_append_upgrades_old_schema_without_losing_values(tmp_path):
    csv_path = tmp_path / "verdict_consensus.csv"
    # an OLD-format file (no universe_id column)
    csv_path.write_text(
        "snapshot_date,strategy,ticker,aristos_verdict,combined_rank,price,"
        "street_mean,n_analysts,target_mean,notes\n"
        "2026-07-05,magic_formula_momentum_v1,AAPL,BUY,24.0,308.63,2.02,42,315.1,\n",
        encoding="utf-8")
    from aristos_council.scoreboard import SnapshotRow
    append_rows([SnapshotRow("2026-08-01", "magic_formula_v1", "growth_40_v1", "MSFT",
                             "BUY", 1.0, 400.0, 1.5, 30, 500.0, "")], csv_path)
    by = {r.ticker: r for r in read_rows(csv_path)}
    assert by["AAPL"].universe_id == ""            # old row backfilled empty, value kept
    assert by["AAPL"].price == 308.63              # existing value preserved
    assert by["MSFT"].universe_id == "growth_40_v1"
