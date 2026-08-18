"""HOLDINGS-RUN-1 — the private holdings grading entrypoint.

The owner's portfolio is graded LOCALLY under every applicable lens plus the F-Score,
with every output kept OUT of this (public) repository. The scout job's venue — Actions
on a public repo, committed results, world-readable logs — is right for scouted tickers
and wrong for a portfolio, so the privacy guards are enforced in CODE and pinned here:

  * a ``--holdings`` / ``--out`` path inside the repo working tree is REFUSED;
  * a whole run writes NOTHING under the repo root (asserted against the real tree);
  * the banner leads BOTH the printed report and the CSV.

Deterministic: a fake adapter, no network, no LLM (every per-strategy run inside
``run_multi_strategy_pipeline`` is ranker-only). The stock fixture is the
``run_rank_pipeline`` cohort shape reused from ``test_multi_strategy_run``; the ETF
fixture is the ``test_etf_lenses`` shape (expense ratio / fund size / momentum).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
TODAY = date(2026, 6, 30)


def _module():
    spec = importlib.util.spec_from_file_location(
        "_grade_holdings_cli", ROOT / "examples" / "grade_holdings.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gh = _module()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
_STOCKS = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400],
              tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350],
              tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}

# F-Score statement series, newest-first. A earns all nine; B is thin on purpose (three
# computable checks, under the 5-check minimum) so the ABSTENTION path is exercised
# alongside a real score.
_F_SERIES = {
    "A": dict(net_income=[100.0, 50.0], total_assets_annual=[1000.0, 900.0],
              operating_cash_flow_annual=[150.0, 120.0],
              long_term_debt_annual=[100.0, 200.0],
              current_assets_annual=[400.0, 300.0],
              current_liabilities_annual=[200.0, 250.0],
              shares_outstanding_annual=[90.0, 100.0],
              gross_profit_annual=[100.0, 75.0]),
    "B": dict(net_income=[10.0, 8.0], total_assets_annual=[500.0, 480.0]),
    "C": dict(net_income=[5.0, 6.0], total_assets_annual=[400.0, 380.0],
              operating_cash_flow_annual=[3.0, 4.0],
              long_term_debt_annual=[50.0, 40.0],
              current_assets_annual=[100.0, 120.0],
              current_liabilities_annual=[90.0, 80.0],
              shares_outstanding_annual=[110.0, 100.0],
              gross_profit_annual=[30.0, 35.0]),
}

# (net_expense_ratio, total_assets, dividend_yield) — the ETF factor inputs.
_ETFS = {
    "VIG": (0.04, 1.29e11, 0.0151),
    "SCHD": (0.06, 9.57e10, 0.033),
    "FVD": (0.62, 8.03e9, 0.0232),
}


class _Adapter(MarketDataAdapter):
    """Three healthy stocks (quote_type EQUITY) and three ETFs (quote_type ETF)."""

    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker in _STOCKS:
            return Fundamentals(ticker=ticker, name=ticker, quote_type="EQUITY",
                                **_STOCKS[ticker], **_F_SERIES[ticker])
        if ticker in _ETFS:
            expense, assets, yld = _ETFS[ticker]
            return Fundamentals(ticker=ticker, name=ticker, quote_type="ETF",
                                net_expense_ratio=expense, total_assets=assets,
                                dividend_yield=yld)
        return Fundamentals(ticker=ticker)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(300)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


MIXED_CSV = (
    "isin,ticker,asset_type,notes\n"
    "US0001,A,Stock,core position\n"
    "US0002,B,Stock,\n"
    "US0003,C,Stock,\n"
    "US0004,VIG,ETF,\n"
    "US0005,SCHD,ETF,\n"
    "US0006,FVD,ETF,\n"
)

SKIP_CSV = (
    "ticker,asset_type\n"
    "A,Stock\n"
    "CASHPOS,Cash\n"
    ",Stock\n"
    "TRUSTX,Investment trust\n"
    "PRIVCO,Private company\n"
    "VIG,ETF\n"
    "A,Stock\n"
    "NOKIND,\n"
)


def _run(tmp_path, text, **kwargs):
    holdings = tmp_path / "holdings.csv"
    holdings.write_text(text, encoding="utf-8")
    out = tmp_path / "graded.csv"
    return gh.grade_holdings(holdings, out, adapter=_Adapter(), today=TODAY,
                             strategies_dir=STRAT_DIR, **kwargs), out


# --------------------------------------------------------------------------- #
# Stock / ETF split -> the right lens sets, both grids
# --------------------------------------------------------------------------- #
def test_lens_sets_are_the_five_stock_and_three_etf_lenses():
    assert gh.STOCK_LENSES == ["conservative_plus_v1", "magic_formula_momentum_v1",
                               "magic_formula_raw_v1", "growth_garp_v2",
                               "financials_v1"]
    assert gh.ETF_LENSES == ["etf_core_v1", "etf_dividend_v1", "etf_growth_v1"]


def test_mixed_csv_produces_both_grids_under_the_right_lenses(tmp_path):
    res, _out = _run(tmp_path, MIXED_CSV)
    assert res.holdings.stocks == ["A", "B", "C"]
    assert res.holdings.etfs == ["VIG", "SCHD", "FVD"]
    assert res.stock_result is not None and res.etf_result is not None
    # each cohort is graded ONLY by its own asset class's lenses
    assert res.stock_result.strategy_ids == gh.STOCK_LENSES
    assert res.etf_result.strategy_ids == gh.ETF_LENSES
    # and the cohorts stay separate — a stock never enters the ETF grid, or vice versa
    assert {r.ticker for r in res.stock_result.rows} == {"A", "B", "C"}
    assert {r.ticker for r in res.etf_result.rows} == {"VIG", "SCHD", "FVD"}
    assert "--- STOCKS ---" in res.report and "--- ETFs ---" in res.report
    # the ETF lenses actually RANK the funds (rank-first, no screens)
    etf_rows = {r.ticker: r for r in res.etf_result.rows}
    assert all(etf_rows["VIG"].cells[sid].status == "ranked" for sid in gh.ETF_LENSES)


def test_stock_only_and_etf_only_files_skip_the_other_pipeline(tmp_path):
    stocks_only, _ = _run(tmp_path, "ticker,asset_type\nA,Stock\nB,Stock\n")
    assert stocks_only.etf_result is None
    assert "--- ETFs --- (none in the holdings file)" in stocks_only.report
    etfs_only, _ = _run(tmp_path, "ticker,asset_type\nVIG,ETF\n")
    assert etfs_only.stock_result is None
    assert "--- STOCKS --- (none in the holdings file)" in etfs_only.report


# --------------------------------------------------------------------------- #
# Skip rules: listed with a reason, graded NOWHERE
# --------------------------------------------------------------------------- #
def test_non_stock_etf_rows_are_skipped_with_reasons_and_graded_nowhere(tmp_path):
    res, out = _run(tmp_path, SKIP_CSV)
    assert res.holdings.stocks == ["A"] and res.holdings.etfs == ["VIG"]

    reasons = {ticker: reason for _i, ticker, _kind, reason in res.holdings.skipped}
    assert set(reasons) == {"CASHPOS", "TRUSTX", "PRIVCO", "NOKIND", "A", ""}
    assert "neither Stock nor ETF" in reasons["CASHPOS"]
    assert "Cash" in reasons["CASHPOS"]                     # the verbatim cell text
    assert "Investment trust" in reasons["TRUSTX"]
    assert "(blank)" in reasons["NOKIND"]                   # blank asset_type, not guessed
    assert "no ticker" in reasons[""]                       # blank ticker row
    assert "duplicate" in reasons["A"]                      # second A row, not re-graded

    graded_tickers = {r.ticker for r in (res.stock_result.rows if res.stock_result else [])}
    graded_tickers |= {r.ticker for r in (res.etf_result.rows if res.etf_result else [])}
    assert graded_tickers == {"A", "VIG"}
    for skipped in ("CASHPOS", "TRUSTX", "PRIVCO", "NOKIND"):
        assert skipped not in graded_tickers
        assert skipped in res.report                        # listed, never dropped

    # in the CSV they carry status=skipped, a reason, and NO lens cell anywhere
    by_ticker = {row["ticker"]: row for row in res.rows}
    for skipped in ("CASHPOS", "TRUSTX", "PRIVCO", "NOKIND"):
        row = by_ticker[skipped]
        assert row["status"] == "skipped" and row["skip_reason"]
        assert not any(sid in row for sid in gh.STOCK_LENSES + gh.ETF_LENSES)
    text = out.read_text(encoding="utf-8")
    assert "SKIPPED" in res.report and "CASHPOS" in text


def test_skipped_rows_are_reported_under_the_skipped_heading(tmp_path):
    res, _out = _run(tmp_path, SKIP_CSV)
    tail = res.report.split("=== SKIPPED")[1]
    for ticker in ("CASHPOS", "TRUSTX", "PRIVCO", "NOKIND"):
        assert ticker in tail
    assert "(blank ticker)" in tail


def test_missing_required_columns_is_a_loud_error():
    with pytest.raises(ValueError) as exc:
        gh.parse_holdings("symbol,kind\nA,Stock\n")
    assert "ticker" in str(exc.value) and "asset_type" in str(exc.value)


def test_asset_type_classification_never_guesses():
    assert gh.classify_asset_type("Stock") == gh.STOCK
    assert gh.classify_asset_type(" etf ") == gh.ETF
    # exact matching only — a fund that is not an ETF is never guessed into ETF lenses
    for unknown in ("Cash", "Investment trust", "Private company", "", None, "bond",
                    "Fund", "Mutual fund", "Equity"):
        assert gh.classify_asset_type(unknown) is None


# --------------------------------------------------------------------------- #
# Path guard — the actual point of the issue
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("inside", [
    "holdings.csv", "./holdings.csv", "reports/holdings.csv", "src/x/../holdings.csv"])
def test_in_repo_paths_are_refused_with_the_risk_named(inside):
    with pytest.raises(gh.PrivacyError) as exc:
        gh.ensure_outside_repo(ROOT / inside, "--out")
    message = str(exc.value)
    assert "INSIDE the repository" in message and "PUBLIC" in message


def test_the_repo_root_itself_is_refused():
    with pytest.raises(gh.PrivacyError):
        gh.ensure_outside_repo(ROOT, "--out")


def test_outside_paths_pass_the_guard(tmp_path):
    resolved = gh.ensure_outside_repo(tmp_path / "holdings.csv", "--holdings")
    assert resolved == (tmp_path / "holdings.csv").resolve()


def test_run_refuses_an_in_repo_holdings_or_out_path(tmp_path):
    outside = tmp_path / "holdings.csv"
    outside.write_text(MIXED_CSV, encoding="utf-8")
    with pytest.raises(gh.PrivacyError) as exc:
        gh.grade_holdings(outside, ROOT / "graded.csv", adapter=_Adapter(),
                          today=TODAY, strategies_dir=STRAT_DIR)
    assert "--out" in str(exc.value)
    with pytest.raises(gh.PrivacyError) as exc:
        gh.grade_holdings(ROOT / "holdings.csv", tmp_path / "graded.csv",
                          adapter=_Adapter(), today=TODAY, strategies_dir=STRAT_DIR)
    assert "--holdings" in str(exc.value)
    assert not (ROOT / "graded.csv").exists()      # refused BEFORE anything was written


def test_a_repo_shaped_tree_is_guarded_by_its_own_root(tmp_path):
    """The guard compares against the repo root it is GIVEN, so a tmp repo-shaped tree
    protects its own contents (and the real repo path is not special-cased)."""
    fake_repo = tmp_path / "aristos-council"
    (fake_repo / "reports").mkdir(parents=True)
    with pytest.raises(gh.PrivacyError):
        gh.ensure_outside_repo(fake_repo / "reports" / "h.csv", "--out",
                               repo_root=fake_repo)
    assert gh.ensure_outside_repo(tmp_path / "h.csv", "--out", repo_root=fake_repo)


def test_cli_refusal_exits_nonzero_without_a_traceback(tmp_path, capsys):
    code = gh.main(["--holdings", str(ROOT / "holdings.csv"),
                    "--out", str(tmp_path / "out.csv")])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Nothing is written under the repo root
# --------------------------------------------------------------------------- #
_TOOLING = {".git", "__pycache__", ".pytest_cache", ".venv"}


def _tree(root: Path) -> set[Path]:
    """Every path under ``root`` except test-runner/tooling scratch (which this
    entrypoint never touches and pytest/pip own)."""
    return {p for p in root.rglob("*")
            if not _TOOLING & set(p.parts)
            and not any(part.endswith(".egg-info") for part in p.parts)}


def test_a_whole_run_writes_nothing_under_the_repo_root(tmp_path):
    committed = [ROOT / "runs", ROOT / "reports", ROOT / "verdicts"]
    before = _tree(ROOT)
    before_committed = {d: _tree(d) for d in committed if d.exists()}

    res, out = _run(tmp_path, MIXED_CSV)
    assert out.exists() and res.rows                 # it DID run and DID write its CSV

    assert _tree(ROOT) == before                     # …and nothing landed in the repo
    for d, paths in before_committed.items():        # named explicitly: the sinks a
        assert _tree(d) == paths                     # council/pipeline run would use


def test_the_default_cache_lives_beside_the_output_never_in_the_repo(tmp_path,
                                                                    monkeypatch):
    """The system default cache dir is repo-RELATIVE, so this entrypoint places its
    cache beside --out (already proven outside the repo) — or omits it entirely."""
    seen = {}

    def _fake_build(*, today, cache_dir):
        seen["cache_dir"] = cache_dir
        return _Adapter()

    monkeypatch.setattr(gh, "build_adapter", _fake_build)
    holdings = tmp_path / "holdings.csv"
    holdings.write_text("ticker,asset_type\nA,Stock\n", encoding="utf-8")
    gh.grade_holdings(holdings, tmp_path / "graded.csv", today=TODAY,
                      strategies_dir=STRAT_DIR)
    assert seen["cache_dir"] == (tmp_path / gh.CACHE_DIR_NAME).resolve()
    assert gh.ROOT not in Path(seen["cache_dir"]).parents

    gh.grade_holdings(holdings, tmp_path / "graded.csv", today=TODAY,
                      strategies_dir=STRAT_DIR, use_cache=False)
    assert seen["cache_dir"] is None                 # --no-cache writes no cache at all

    with pytest.raises(gh.PrivacyError):             # an in-repo --cache-dir is refused
        gh.grade_holdings(holdings, tmp_path / "graded.csv", today=TODAY,
                          strategies_dir=STRAT_DIR, cache_dir=ROOT / ".cache")


# --------------------------------------------------------------------------- #
# The banner leads both outputs
# --------------------------------------------------------------------------- #
def test_banner_leads_the_printed_report(tmp_path):
    res, _out = _run(tmp_path, MIXED_CSV)
    head = res.report.splitlines()[:3]
    assert gh.BANNER in "\n".join(head)
    assert "bottom quintile of YOUR OWN holdings" in gh.BANNER
    assert "'weakest holding', not 'sell it'" in gh.BANNER


def test_banner_leads_the_csv(tmp_path):
    _res, out = _run(tmp_path, MIXED_CSV)
    lines = out.read_text(encoding="utf-8").splitlines()
    banner_lines = [ln for ln in lines if ln.startswith("#")]
    assert banner_lines and lines[0].startswith("#")
    assert "RELATIVE to this cohort" in banner_lines[0]
    joined = " ".join(ln.lstrip("# ") for ln in banner_lines)
    for fragment in ("bottom quintile of YOUR OWN holdings", "weakest holding"):
        assert fragment in joined
    header = lines[len(banner_lines)]
    assert header.startswith("status,asset_type,ticker,name")


def test_csv_carries_every_lens_cell_for_both_asset_classes(tmp_path):
    _res, out = _run(tmp_path, MIXED_CSV)
    import csv as _csv
    text = out.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))
    rows = {r["ticker"]: r for r in _csv.DictReader(body.splitlines())}
    assert rows["A"]["asset_type"] == "Stock" and rows["VIG"]["asset_type"] == "ETF"
    for sid in gh.STOCK_LENSES:
        assert rows["A"][sid] and rows["A"][sid] != "—"
    for sid in gh.ETF_LENSES:
        assert rows["VIG"][f"{sid}_status"] == "ranked"


# --------------------------------------------------------------------------- #
# F-Score — display only, abstention honoured
# --------------------------------------------------------------------------- #
def test_f_score_is_reported_per_stock_and_never_for_etfs(tmp_path):
    res, _out = _run(tmp_path, MIXED_CSV)
    assert set(res.f_scores) == {"A", "B", "C"}          # stocks only
    assert res.f_scores["A"].score == 9                  # all nine checks earned
    assert "PIOTROSKI F-SCORE (display only" in res.report
    assert "9/9" in res.report
    by_ticker = {row["ticker"]: row for row in res.rows}
    assert by_ticker["A"]["f_score"] == "9/9"
    assert by_ticker["A"]["f_score_computed"] == "9/9"
    assert by_ticker["A"]["f_score_checks"] == "+" * 9
    assert by_ticker["VIG"]["f_score"] == ""             # a fund has no F-Score


def test_thin_data_abstains_instead_of_scoring_zero(tmp_path):
    res, _out = _run(tmp_path, MIXED_CSV)
    b = res.f_scores["B"]
    assert b.score is None and b.computed < 5            # C<5 -> abstention, not a 0
    assert "abstained" in gh.f_score_display(b)
    assert "?" in gh.f_score_glyphs(b)                   # unavailable ≠ failed check
    by_ticker = {row["ticker"]: row for row in res.rows}
    assert "abstained" in by_ticker["B"]["f_score"]
    assert "0/9" not in by_ticker["B"]["f_score"]


# --------------------------------------------------------------------------- #
# CLI wiring + the never-in-Actions statement
# --------------------------------------------------------------------------- #
def test_cli_args():
    args = gh.parse_args(["--holdings", "h.csv", "--out", "o.csv"])
    assert args.holdings == "h.csv" and args.out == "o.csv"
    assert args.cache_dir is None and args.no_cache is False
    assert gh.parse_args(["--holdings", "h.csv", "--out", "o.csv",
                          "--no-cache"]).no_cache is True


def test_script_documents_why_it_is_never_a_github_action():
    source = (ROOT / "examples" / "grade_holdings.py").read_text(encoding="utf-8")
    assert "WHY THIS IS NOT A GITHUB ACTION" in source
    for fragment in ("world-readable", "PUBLIC"):
        assert fragment in source


def test_the_entrypoint_is_wired_into_no_workflow():
    workflows = ROOT / ".github" / "workflows"
    paths = sorted(workflows.rglob("*.y*ml")) if workflows.exists() else []
    for path in paths:
        assert "grade_holdings" not in path.read_text(encoding="utf-8"), path.name


def test_gitignore_backstops_the_conventional_private_filenames():
    entries = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "holdings*.csv" in entries and "holdings*.txt" in entries
