"""Piotroski F-Score (PIOTROSKI-1) — the nine checks, both registries, the adapter.

The score is deterministic math in ``tools/screening.py`` with TWO consumers: the
rankable ``piotroski_f_score`` factor and the ``min_f_score`` screen criterion. These
tests pin the checks, the three documented conventions (strict zero-LTD, ending-assets
denominators, unavailable≠failed), the C<5 abstention, and the fact that NO strategy
YAML adopts either entry in this PR.
"""

from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

# pandas ships with the yfinance extra and is needed ONLY by the adapter-mapping tests
# at the bottom. Gated per-test, not module-wide: a module-level importorskip would
# silently skip the pure F-Score math too, which must run in a bare install.
try:                                       # pragma: no cover - environment-dependent
    import pandas as pd
except ImportError:                        # pragma: no cover
    pd = None

requires_pandas = pytest.mark.skipif(
    pd is None, reason="pandas (yfinance extra) not installed")

from aristos_council.company_check import format_factor_value  # noqa: E402
from aristos_council.data.adapter import Fundamentals  # noqa: E402
from aristos_council.factors import (  # noqa: E402
    FACTOR_REGISTRY,
    PRICE_DERIVED_FACTORS,
    FactorInputs,
)
from aristos_council.tools.criteria.registry import (  # noqa: E402
    REGISTRY,
    CriterionSelection,
    Evidence,
    validate_selections,
)
from aristos_council.tools.screening import piotroski_f_score  # noqa: E402

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


# --------------------------------------------------------------------------- #
# Fixtures — every series NEWEST-FIRST ([0] current year, [1] prior year)
# --------------------------------------------------------------------------- #
def _fund(ticker: str = "TEST", **series) -> Fundamentals:
    return Fundamentals(ticker=ticker, **series)


def _strong(ticker: str = "STRONG") -> Fundamentals:
    """All nine checks satisfied.

    ROA 100/1000 = 10.0% vs 50/900 = 5.6% (positive AND improved); OCF 150 > 0 and
    150 > net income 100; LTD ratio 0.100 < 0.222; current ratio 2.00 > 1.20; shares
    90 <= 100; gross margin 50.0% > 37.5%; asset turnover 1.00 > 0.89.
    """
    return _fund(
        ticker,
        net_income=[100.0, 50.0],
        total_assets_annual=[1000.0, 900.0],
        operating_cash_flow_annual=[150.0, 120.0],
        long_term_debt_annual=[100.0, 200.0],
        current_assets_annual=[400.0, 300.0],
        current_liabilities_annual=[200.0, 250.0],
        shares_outstanding_annual=[90.0, 100.0],
        gross_profit_annual=[500.0, 300.0],
        total_revenue=[1000.0, 800.0],
    )


def _deteriorating() -> Fundamentals:
    """The Galderma shape: still PROFITABLE, but the cash and the trends are going the
    wrong way — OCF positive yet BELOW net income and falling, ROA down, leverage up,
    liquidity down, shares issued, margin and turnover down. Only checks 1 and 2 score.
    """
    return _fund(
        "DETERIORATING",
        net_income=[80.0, 100.0],
        total_assets_annual=[1200.0, 1000.0],
        operating_cash_flow_annual=[60.0, 90.0],
        long_term_debt_annual=[400.0, 300.0],
        current_assets_annual=[300.0, 400.0],
        current_liabilities_annual=[300.0, 300.0],
        shares_outstanding_annual=[110.0, 100.0],
        gross_profit_annual=[400.0, 400.0],
        total_revenue=[1000.0, 900.0],
    )


def _five_computable() -> Fundamentals:
    """Exactly FIVE computable checks — the minimum that still scores.

    Present: net income, total assets, OCF, revenue -> checks 1, 2, 3, 4, 9.
    Absent: long-term debt, current assets/liabilities, shares, gross profit ->
    checks 5, 6, 7, 8 UNAVAILABLE.
    """
    return _fund(
        "FIVE",
        net_income=[100.0, 50.0],
        total_assets_annual=[1000.0, 900.0],
        operating_cash_flow_annual=[150.0, 120.0],
        total_revenue=[1000.0, 800.0],
    )


def _four_computable() -> Fundamentals:
    """FOUR computable checks (the five-fixture minus revenue, which drops the asset-
    turnover check) — below the minimum, so the whole score ABSTAINS."""
    return _fund(
        "FOUR",
        net_income=[100.0, 50.0],
        total_assets_annual=[1000.0, 900.0],
        operating_cash_flow_annual=[150.0, 120.0],
    )


def _zero_ltd() -> Fundamentals:
    """A debt-free firm: long-term debt 0 in BOTH years."""
    return replace(_strong("ZEROLTD"), long_term_debt_annual=[0.0, 0.0])


def _checks(f: Fundamentals) -> dict[str, bool | None]:
    return dict(piotroski_f_score(f).checks)


# --------------------------------------------------------------------------- #
# The nine checks
# --------------------------------------------------------------------------- #
def test_strong_fixture_scores_nine_of_nine_with_nothing_unavailable():
    r = piotroski_f_score(_strong())
    assert r.score == 9
    assert (r.computed, r.unavailable) == (9, 0)
    assert all(v is True for _, v in r.checks)
    assert r.note == "F-Score 9/9 — computed from 9 checks, 0 unavailable"


def test_deteriorating_fixture_scores_low_and_check_four_is_the_cash_quality_catch():
    r = piotroski_f_score(_deteriorating())
    checks = dict(r.checks)
    # Check 2 still scores — the cash flow IS positive. Check 4 is what catches the
    # accrual quality: OCF 60 does NOT exceed net income 80.
    assert checks["ocf_positive"] is True
    assert checks["ocf_exceeds_net_income"] is False
    assert checks["roa_positive"] is True            # still profitable
    for name in ("roa_improved", "ltd_ratio_decreased", "current_ratio_improved",
                 "no_new_share_issuance", "gross_margin_improved",
                 "asset_turnover_improved"):
        assert checks[name] is False, name
    assert r.score == 2
    assert (r.computed, r.unavailable) == (9, 0)


def test_roa_and_asset_turnover_use_same_year_ending_assets():
    # Documented simplification: the denominator is the SAME-YEAR (ending) total
    # assets, not a beginning-of-year / average base. ROA[0] = 100/1000, ROA[1] =
    # 50/900 -> improved. Under an average-assets convention ROA[1] would need a
    # third year of assets, which this fixture does not have — yet check 3 computes.
    assert _checks(_strong())["roa_improved"] is True
    assert _checks(_strong())["asset_turnover_improved"] is True


def test_negative_total_assets_makes_the_asset_ratios_unavailable_not_failed():
    # A non-positive denominator is a data problem, not a verdict: the checks that
    # need it count UNAVAILABLE (null≠false, project rule 3), never as failures.
    checks = _checks(replace(_strong(), total_assets_annual=[-10.0, 900.0]))
    assert checks["roa_positive"] is None
    assert checks["roa_improved"] is None
    assert checks["asset_turnover_improved"] is None
    assert checks["ltd_ratio_decreased"] is None      # LTD ratio shares the denominator
    assert checks["current_ratio_improved"] is True   # untouched by total assets


# --------------------------------------------------------------------------- #
# Convention: STRICT zero long-term debt
# --------------------------------------------------------------------------- #
def test_zero_long_term_debt_scores_no_point_and_counts_as_COMPUTED():
    # Strict Piotroski: a ratio of 0.0 in both years did NOT decrease, so check 5
    # scores no point — and it is COMPUTED, not unavailable (comparability with
    # published F-Scores beats economic charity).
    r = piotroski_f_score(_zero_ltd())
    checks = dict(r.checks)
    assert checks["ltd_ratio_decreased"] is False     # NOT None
    assert r.score == 8                               # the other eight still score
    assert (r.computed, r.unavailable) == (9, 0)


def test_absent_long_term_debt_series_is_unavailable_not_a_zero():
    # An ABSENT statement line is not a zero-debt firm: the check abstains.
    r = piotroski_f_score(replace(_strong(), long_term_debt_annual=[]))
    assert dict(r.checks)["ltd_ratio_decreased"] is None
    assert (r.score, r.computed, r.unavailable) == (8, 8, 1)


# --------------------------------------------------------------------------- #
# Computability / abstention (C < 5)
# --------------------------------------------------------------------------- #
def test_exactly_five_computable_checks_scores_and_does_not_abstain():
    r = piotroski_f_score(_five_computable())
    assert r.score == 5
    assert (r.computed, r.unavailable) == (5, 4)
    assert r.note == (
        "F-Score 5/9 — computed from 5 checks, 4 unavailable (unavailable: "
        "ltd_ratio_decreased, current_ratio_improved, no_new_share_issuance, "
        "gross_margin_improved)")


def test_four_computable_checks_abstains_entirely():
    r = piotroski_f_score(_four_computable())
    assert r.score is None                            # never a partial score
    assert (r.computed, r.unavailable) == (4, 5)
    assert "not computable" in r.note and "minimum 5" in r.note


def test_no_fundamentals_abstains():
    r = piotroski_f_score(None)
    assert r.score is None
    assert (r.computed, r.unavailable) == (0, 9)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_repeated_evaluation_is_identical():
    f = _strong()
    first = piotroski_f_score(f)
    for _ in range(5):
        assert piotroski_f_score(f) == first


def test_two_independently_built_equal_fundamentals_score_identically():
    a, b = _strong("A"), _strong("B")
    ra, rb = piotroski_f_score(a), piotroski_f_score(b)
    assert (ra.score, ra.unavailable, ra.note) == (rb.score, rb.unavailable, rb.note)
    assert ra.checks == rb.checks


def test_reversed_series_is_not_silently_treated_as_equivalent():
    # Annual series are NEWEST-FIRST by contract. A reversed series describes a
    # different (deteriorating) firm and must score differently — the math never
    # re-sorts to "repair" a caller that got the order wrong.
    strong = _strong()
    reversed_ = replace(strong, **{
        name: list(reversed(getattr(strong, name)))
        for name in ("net_income", "total_assets_annual",
                     "operating_cash_flow_annual", "long_term_debt_annual",
                     "current_assets_annual", "current_liabilities_annual",
                     "shares_outstanding_annual", "gross_profit_annual",
                     "total_revenue")
    })
    assert piotroski_f_score(strong).score == 9
    assert piotroski_f_score(reversed_).score == 3


# --------------------------------------------------------------------------- #
# PART 3 — the rankable FACTOR
# --------------------------------------------------------------------------- #
def test_factor_is_registered_high_direction_and_computes_the_score():
    fdef = FACTOR_REGISTRY["piotroski_f_score"]
    assert fdef.direction == "high"
    assert fdef.label == "Piotroski F-Score (0-9)"
    assert fdef.fn(FactorInputs(ticker="STRONG", fundamentals=_strong())) == 9.0
    assert fdef.fn(FactorInputs(ticker="DET", fundamentals=_deteriorating())) == 2.0


def test_factor_abstains_below_five_computable_checks():
    fdef = FACTOR_REGISTRY["piotroski_f_score"]
    assert fdef.fn(FactorInputs(ticker="FOUR", fundamentals=_four_computable())) is None
    assert fdef.fn(FactorInputs(ticker="NONE")) is None


def test_factor_is_not_price_derived():
    # Not computable point-in-time from closes -> excluded from the backtest sleeve.
    assert "piotroski_f_score" not in PRICE_DERIVED_FACTORS


def test_factor_value_renders_out_of_nine():
    assert format_factor_value("piotroski_f_score", 7.0) == "7/9"
    assert format_factor_value("piotroski_f_score", 9.0) == "9/9"
    assert format_factor_value("piotroski_f_score", None) == "—"
    # unchanged for everything else
    assert format_factor_value("momentum_12m", 7.11) == "+711%"


# --------------------------------------------------------------------------- #
# PART 4 — the screen CRITERION
# --------------------------------------------------------------------------- #
def _run(f: Fundamentals | None, threshold: float):
    return REGISTRY["min_f_score"].fn(Evidence(fundamentals=f), threshold)


def test_criterion_self_describes_int_threshold_zero_to_nine():
    crit = REGISTRY["min_f_score"]
    assert crit.label == "Minimum Piotroski F-Score"
    tp = crit.threshold_param
    assert (tp.type, tp.min, tp.max, tp.step, tp.default) == ("int", 0, 9, 1, 5)
    assert crit.requires == ("fundamentals",)
    assert "total_assets_annual" in crit.fundamentals_fields
    assert "net_income" in crit.fundamentals_fields
    assert "operating_cash_flow_annual" in crit.fundamentals_fields


def test_criterion_passes_fails_and_reports_the_accounting():
    ok = _run(_strong(), 5)
    assert ok.passed is True and ok.observed == 9.0 and ok.threshold == 5
    assert ok.note == "F-Score 9/9 — computed from 9 checks, 0 unavailable"

    bad = _run(_deteriorating(), 5)
    assert bad.passed is False and bad.observed == 2.0

    edge = _run(_five_computable(), 5)              # score == threshold -> passes
    assert edge.passed is True and edge.observed == 5.0
    assert "4 unavailable" in edge.note


def test_criterion_abstains_below_five_computable_checks():
    r = _run(_four_computable(), 5)
    assert r.passed is None                          # NOT-EVAL, never a failed screen
    assert r.observed is None
    assert "abstained" in r.note
    assert _run(None, 5).passed is None


def test_criterion_threshold_bounds_are_validated_up_front():
    assert validate_selections([CriterionSelection("min_f_score", 9)]) == []
    assert validate_selections([CriterionSelection("min_f_score", 0)]) == []
    problems = validate_selections([CriterionSelection("min_f_score", 10)])
    assert problems and "out of range" in problems[0]


def test_no_strategy_yaml_adopts_the_factor_or_the_criterion_in_this_pr():
    for path in sorted(STRAT_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert "min_f_score" not in text, path.name
        assert "piotroski" not in text.lower(), path.name


# --------------------------------------------------------------------------- #
# PART 1 — the adapter mapping (label aliases; absent statement -> empty lists)
# --------------------------------------------------------------------------- #
class _FakeTicker:
    def __init__(self, info, income, balance):
        self.info = info
        self.financials = income
        self.balance_sheet = balance
        self.cashflow = None
        self.dividends = None

    def history(self, *a, **k):
        return None


def _adapter(monkeypatch, income, balance):
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda symbol: _FakeTicker({"longName": "Test Co"},
                                                income, balance)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    from aristos_council.data.yfinance_adapter import YFinanceAdapter
    return YFinanceAdapter()


def _frame(rows: dict[str, list[float]]):
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]   # newest-first
    return pd.DataFrame(list(rows.values()), index=list(rows), columns=cols)


@requires_pandas
def test_adapter_maps_the_new_series_from_canonical_labels(monkeypatch):
    income = _frame({"Total Revenue": [1000.0, 800.0], "Gross Profit": [500.0, 300.0]})
    balance = _frame({
        "Total Assets": [1000.0, 900.0],
        "Long Term Debt": [100.0, 200.0],
        "Current Assets": [400.0, 300.0],
        "Current Liabilities": [200.0, 250.0],
        "Ordinary Shares Number": [90.0, 100.0],
    })
    f = _adapter(monkeypatch, income, balance).get_fundamentals("TEST")
    assert f.total_assets_annual == [1000.0, 900.0]
    assert f.long_term_debt_annual == [100.0, 200.0]
    assert f.current_assets_annual == [400.0, 300.0]
    assert f.current_liabilities_annual == [200.0, 250.0]
    assert f.shares_outstanding_annual == [90.0, 100.0]
    assert f.gross_profit_annual == [500.0, 300.0]
    # total_assets (the ETF net-assets SCALAR) is a different field and stays None —
    # the annual balance-sheet series must never overload it.
    assert f.total_assets is None


@requires_pandas
def test_adapter_maps_the_new_series_from_label_aliases(monkeypatch):
    # yfinance drifts between labels across versions; the alias-tolerant helper
    # picks up the second spelling of each line.
    income = _frame({"Total Revenue": [1000.0, 800.0], "Gross Profit": [500.0, 300.0]})
    balance = _frame({
        "Total Assets": [1000.0, 900.0],
        "Long Term Debt And Capital Lease Obligation": [100.0, 200.0],
        "Total Current Assets": [400.0, 300.0],
        "Total Current Liabilities": [200.0, 250.0],
        "Share Issued": [90.0, 100.0],
    })
    f = _adapter(monkeypatch, income, balance).get_fundamentals("TEST")
    assert f.long_term_debt_annual == [100.0, 200.0]
    assert f.current_assets_annual == [400.0, 300.0]
    assert f.current_liabilities_annual == [200.0, 250.0]
    assert f.shares_outstanding_annual == [90.0, 100.0]


@requires_pandas
def test_adapter_absent_statements_yield_empty_lists_not_an_exception(monkeypatch):
    f = _adapter(monkeypatch, None, None).get_fundamentals("TEST")
    for name in ("total_assets_annual", "long_term_debt_annual",
                 "current_assets_annual", "current_liabilities_annual",
                 "shares_outstanding_annual", "gross_profit_annual"):
        assert getattr(f, name) == [], name
    # ...and the score honestly abstains rather than reading as a 0/9 company.
    assert piotroski_f_score(f).score is None
