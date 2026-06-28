"""Criterion registry tests, with the EQUIVALENCE TEST as the safety net.

The registry refactor's whole correctness claim is: the generic, registry-driven
screen produces BYTE-IDENTICAL results to the original hardcoded
``run_strategy_screen``. test_equivalent_to_legacy_screen pins that
field-for-field on a fixed set of fundamentals shapes (JNJ/MO/BRK-B/O + the edge
cases that exercise pass / fail / null / no-dividend / unverifiable paths).
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DividendEvent, Fundamentals
from aristos_council.tools.criteria.registry import (
    REGISTRY,
    Criterion,
    CriterionSelection,
    Evidence,
    required_evidence,
    run_screen,
    validate_selections,
)
from aristos_council.tools.screening import run_strategy_screen

# The dividend strategy's selections at the shipped v1 thresholds.
DIVIDEND = [
    CriterionSelection("min_dividend_yield", 0.025),
    CriterionSelection("max_payout_ratio", 0.75),
    CriterionSelection("min_market_cap", 10_000_000_000),
    CriterionSelection("min_dividend_growth_streak", 25),
]


def _fund(ticker, **kw) -> Fundamentals:
    base = dict(ticker=ticker)
    base.update(kw)
    return Fundamentals(**base)  # type: ignore[arg-type]


def _annual(start_year: int, amounts: list[float]) -> list[DividendEvent]:
    return [DividendEvent(ex_date=date(start_year + i, 6, 1), amount=a)
            for i, a in enumerate(amounts)]


def _monthly(start_year: int, years: int, base: float, step: float):
    out = []
    for y in range(years):
        for m in range(1, 13):
            out.append(DividendEvent(ex_date=date(start_year + y, m, 1),
                                     amount=base + step * y))
    return out


# (fundamentals, dividends, last_close) shapes exercising every branch.
_long = [1.0 + 0.1 * i for i in range(30)]   # 30 rising years -> streak passes
_short = [1.0 + 0.05 * i for i in range(16)]  # 16 rising years -> streak fails 25

FIXTURES = [
    # JNJ: payer, passes yield/payout/mcap, long streak
    (_fund("JNJ", market_cap=3.8e11, dividend_per_share=4.96, payout_ratio=0.45),
     _annual(1996, _long), 155.0),
    # MO: high-yield payer, payout FAILS the ceiling, shorter streak
    (_fund("MO", market_cap=7.7e10, dividend_per_share=3.92, payout_ratio=0.88),
     _annual(2010, _short), 44.0),
    # BRK-B: no current dividend, no history (yield FAIL, payout NOT-EVAL)
    (_fund("BRK-B", market_cap=8.8e11), [], 410.0),
    # O: monthly payer, high yield, payout near ceiling
    (_fund("O", market_cap=5.0e10, dividend_per_share=3.08, payout_ratio=0.76),
     _monthly(2014, 10, 0.20, 0.01), 55.0),
    # payer but NO price -> yield UNVERIFIABLE
    (_fund("NOPRICE", market_cap=2e10, dividend_per_share=2.0, payout_ratio=0.5),
     _annual(2010, _short), None),
    # payer, EMPTY history -> streak null (unverifiable)
    (_fund("EMPTY", market_cap=2e10, dividend_per_share=2.0, payout_ratio=0.5),
     [], 100.0),
    # negative payout -> payout FAIL (not unverifiable)
    (_fund("NEG", market_cap=2e10, dividend_per_share=2.0, payout_ratio=-0.2),
     _annual(2018, [1.0, 1.1, 1.2, 1.3, 1.4]), 100.0),
    # missing market cap -> market_cap NOT-EVAL
    (_fund("NOMC", dividend_per_share=2.0, payout_ratio=0.5),
     _annual(1996, _long), 100.0),
]


@pytest.mark.parametrize("fund,divs,last_close", FIXTURES,
                         ids=[f[0].ticker for f in FIXTURES])
def test_equivalent_to_legacy_screen(fund, divs, last_close):
    """Registry-driven screen == original hardcoded screen, field-for-field."""
    new = run_screen(DIVIDEND, Evidence(fund, divs, last_close),
                     ticker=fund.ticker)
    legacy = run_strategy_screen(
        fund, divs, min_yield=0.025, max_payout=0.75,
        min_market_cap=10_000_000_000, min_growth_years=25,
        last_close=last_close,
    )
    assert new == legacy                      # whole ScreenResult (frozen dataclass)
    assert new.criteria == legacy.criteria    # explicit: per-criterion identity
    assert new.flags == legacy.flags


# --------------------------------------------------------------------------- #
# Registry contents + validation
# --------------------------------------------------------------------------- #
def test_registry_holds_dividend_and_growth_criteria():
    assert set(REGISTRY) == {
        # dividend (4A)
        "min_dividend_yield", "max_payout_ratio",
        "min_market_cap", "min_dividend_growth_streak",
        # growth / quality (4B)
        "min_revenue_cagr", "min_roic", "max_peg_ratio",
    }
    assert all(isinstance(c, Criterion) for c in REGISTRY.values())


def test_each_criterion_declares_required_evidence():
    assert REGISTRY["min_dividend_yield"].requires == ("fundamentals",)
    assert REGISTRY["min_dividend_growth_streak"].requires == ("dividends",)


def test_every_criterion_self_describes_label_and_param_spec():
    """The hook the dynamic Strategy tab (4B) reads: each criterion exposes a
    human label and a param spec a UI can render without strategy-specific code."""
    for name, crit in REGISTRY.items():
        assert isinstance(crit.label, str) and crit.label, name
        assert crit.params, name                      # at least one parameter
        for p in crit.params:                         # each param fully described
            assert p.name and p.type in ("float", "int", "bool"), (name, p)
            if p.type in ("float", "int"):
                assert p.min is not None and p.step is not None, (name, p)
        # the numeric threshold is declared with type + bounds/step + default
        tp = crit.threshold_param
        assert tp is not None and tp.type in ("float", "int"), name
        assert tp.min is not None and tp.step is not None, name
        assert tp.default is not None, name           # UI pre-fill value
        # policy flags are declared as bool (the unverifiable-blocks flag)
        assert any(p.name == "unverifiable_blocks" and p.type == "bool"
                   for p in crit.params), name


def test_threshold_param_bounds_match_known_criteria():
    # yield is a decimal in [0, 1]; the streak is an integer parameter
    assert REGISTRY["min_dividend_yield"].threshold_param.max == 1.0
    assert REGISTRY["min_dividend_growth_streak"].threshold_param.type == "int"


def test_validate_ok_for_dividend_strategy():
    assert validate_selections(DIVIDEND) == []


def test_validate_flags_unknown_criterion():
    problems = validate_selections([CriterionSelection("ebitda_coverage", 3.0)])
    assert any("unknown criterion" in p for p in problems)


def test_validate_flags_out_of_range_threshold():
    # yield must be in [0, 1]
    problems = validate_selections([CriterionSelection("min_dividend_yield", 1.5)])
    assert any("out of range" in p for p in problems)


def test_required_evidence_dividend_needs_dividends_growth_does_not():
    # drives strategy-scoped tool selection (4E)
    assert "dividends" in required_evidence(DIVIDEND)        # has growth-streak
    growth = [
        CriterionSelection("min_revenue_cagr", 0.10),
        CriterionSelection("min_roic", 0.12),
        CriterionSelection("max_peg_ratio", 2.0),
        CriterionSelection("min_market_cap", 5_000_000_000),
    ]
    assert "dividends" not in required_evidence(growth)
    assert "fundamentals" in required_evidence(growth)


def test_validate_flags_missing_evidence():
    # the streak criterion needs dividend history; deny it and it must complain
    problems = validate_selections(
        [CriterionSelection("min_dividend_growth_streak", 25)],
        available=("fundamentals", "last_close"),
    )
    assert any("requires evidence not available" in p for p in problems)


def test_run_screen_raises_on_unknown_criterion():
    with pytest.raises(KeyError):
        run_screen([CriterionSelection("nope", 1.0)], Evidence(), ticker="X")


# --------------------------------------------------------------------------- #
# Growth / quality criteria (4B) — behavior + the edge cases the probe surfaced.
# Run each criterion through its registered function for clarity.
# --------------------------------------------------------------------------- #
def _crit(name, fund, threshold):
    return REGISTRY[name].fn(Evidence(fundamentals=fund), threshold)


def test_revenue_cagr_passes_and_fails_around_threshold():
    # 3y CAGR from [146,121,110,100]: (1.46)^(1/3)-1 ~ 0.1346
    f = _fund("GRW", total_revenue=[146.0, 121.0, 110.0, 100.0])
    assert _crit("min_revenue_cagr", f, 0.10).passed is True
    assert _crit("min_revenue_cagr", f, 0.20).passed is False
    obs = _crit("min_revenue_cagr", f, 0.10).observed
    assert abs(obs - 0.1346) < 0.01


def test_revenue_cagr_not_eval_on_short_history():
    # fewer than years+1 (=4) clean points -> NOT-EVAL, no crash
    f = _fund("SHORT", total_revenue=[120.0, 100.0])
    r = _crit("min_revenue_cagr", f, 0.10)
    assert r.passed is None and r.observed is None
    assert "insufficient revenue history" in r.note


def test_roic_uses_provided_invested_capital_for_negative_equity():
    # MO-shape: negative book equity, but a sane PROVIDED invested_capital line.
    # ROIC must compute off invested_capital (not debt+equity) and stay sane.
    mo = _fund("MO", operating_income=[12000.0], tax_provision=[2500.0],
               pretax_income=[10000.0], invested_capital=[30000.0])
    r = _crit("min_roic", mo, 0.12)
    assert r.passed is not None              # evaluated, not crashed
    assert r.observed is not None and r.observed > 0   # sane positive ROIC


def test_roic_fails_on_negative_nopat():
    # AMZN-2022-shape: negative operating income -> negative NOPAT -> ROIC<0 FAIL
    amzn = _fund("AMZN", operating_income=[-2000.0], tax_provision=[0.0],
                 pretax_income=[-3000.0], invested_capital=[150000.0])
    r = _crit("min_roic", amzn, 0.12)
    assert r.passed is False                 # a determination, not NOT-EVAL
    assert r.observed < 0


def test_roic_not_eval_on_missing_invested_capital():
    f = _fund("NOIC", operating_income=[1000.0], tax_provision=[200.0],
              pretax_income=[800.0], invested_capital=[])
    r = _crit("min_roic", f, 0.12)
    assert r.passed is None
    assert "invested_capital" in r.note


def test_peg_not_eval_on_negative_earnings():
    # AMZN-2022-shape: no positive P/E -> PEG NOT-EVAL (even with revenue growth)
    amzn = _fund("AMZN", total_revenue=[146.0, 121.0, 110.0, 100.0], pe_ratio=None)
    r = _crit("max_peg_ratio", amzn, 2.0)
    assert r.passed is None
    assert "P/E" in r.note


def test_peg_evaluates_with_pe_and_growth():
    # PE 25, 3y CAGR ~0.1346 -> PEG = 25/13.46 ~ 1.86 -> passes <= 2.0
    f = _fund("GARP", total_revenue=[146.0, 121.0, 110.0, 100.0], pe_ratio=25.0)
    r = _crit("max_peg_ratio", f, 2.0)
    assert r.passed is True
    assert abs(r.observed - 1.86) < 0.1


def test_peg_not_eval_on_zero_growth():
    # flat revenue -> CAGR 0 -> PEG undefined -> NOT-EVAL
    f = _fund("FLAT", total_revenue=[100.0, 100.0, 100.0, 100.0], pe_ratio=20.0)
    r = _crit("max_peg_ratio", f, 2.0)
    assert r.passed is None


def test_peg_fails_when_earnings_present_but_not_growing():
    # FIX-1b: operating income PRESENT (full series) but declining -> the PEG
    # criterion FAILS (passed=False), it does NOT abstain. A non-growing GARP name
    # must register a real failure, not a laundered NOT-EVAL.
    f = _fund("DECLINE", total_revenue=[130.0, 120.0, 110.0, 100.0],
              operating_income=[85.0, 90.0, 95.0, 100.0],   # declining each year
              pe_ratio=20.0)
    r = _crit("max_peg_ratio", f, 2.0)
    assert r.passed is False and r.observed is None
    assert "earnings not growing" in r.note


def test_lmt_shape_revenue_fail_plus_non_growing_earnings_two_real_fails():
    # FIX-1b regression LOCK (the LMT case): revenue CAGR below threshold (a real
    # fail) AND operating income not growing -> max_peg_ratio ALSO fails. TWO genuine
    # fails, so the name can't be softened to a single-fail HOLD by
    # partial_pass_allows_hold — the SELL-laundered-into-HOLD bug the fix closes.
    lmt = _fund("LMT", total_revenue=[114.0, 109.0, 105.0, 100.0],  # ~4.46% CAGR
                operating_income=[80.0, 90.0, 95.0, 100.0],         # declining
                pe_ratio=17.0)
    rev = _crit("min_revenue_cagr", lmt, 0.10)
    peg = _crit("max_peg_ratio", lmt, 2.0)
    assert rev.passed is False                  # revenue CAGR ~4.46% < 10% threshold
    assert peg.passed is False                  # earnings not growing -> real fail
    assert peg.observed is None and "earnings not growing" in peg.note


def test_non_usd_listing_abstains_market_cap_but_ratios_still_evaluate():
    """SK Hynix shape (KRW listing) through run_screen: the USD-denominated
    min_market_cap must NOT-EVAL with a currency note, while currency-invariant
    ratio criteria (revenue_cagr, roic) evaluate normally."""
    hynix = _fund(
        "000660.KS", currency="KRW", financial_currency="KRW",
        market_cap=1.69e15,                                  # KRW, not USD
        total_revenue=[146.0, 121.0, 110.0, 100.0],          # ratio -> invariant
        operating_income=[30000.0], tax_provision=[6000.0],
        pretax_income=[25000.0], invested_capital=[120000.0])
    selections = [
        CriterionSelection("min_market_cap", 10_000_000_000),
        CriterionSelection("min_revenue_cagr", 0.10),
        CriterionSelection("min_roic", 0.12),
    ]
    result = run_screen(selections, Evidence(fundamentals=hynix),
                        ticker="000660.KS")
    by = {c.name: c for c in result.criteria}
    mc = by["min_market_cap"]
    assert mc.passed is None and mc.observed is None         # honest abstention
    assert "KRW" in mc.note and "not USD" in mc.note
    assert any("unverifiable:min_market_cap" in fl for fl in result.flags)
    # ratios are currency-invariant -> they evaluate, not abstain
    assert by["min_revenue_cagr"].passed is True
    assert by["min_roic"].passed is not None


def test_usd_listing_market_cap_unchanged_through_run_screen():
    # A USD-currency name screens exactly as an un-tagged one (regression guard).
    usd = _fund("AAA", currency="USD", market_cap=2e10)
    untagged = _fund("AAA", market_cap=2e10)
    sel = [CriterionSelection("min_market_cap", 1e10)]
    a = run_screen(sel, Evidence(fundamentals=usd), ticker="AAA").criteria[0]
    b = run_screen(sel, Evidence(fundamentals=untagged), ticker="AAA").criteria[0]
    assert a.passed is True and b.passed is True


def test_peg_and_revenue_cagr_share_one_cagr_window():
    # PEG is defined as P/E / (revenue CAGR x 100), where that CAGR is the SAME
    # in-house window min_revenue_cagr uses. Both criteria read the one module
    # constant _REVENUE_CAGR_YEARS, so the windows can never diverge. Pin it
    # behaviourally: the revenue-CAGR observed and the CAGR implied by the PEG
    # denominator are identical, and both match an independent CAGR over that
    # window.
    from aristos_council.tools.criteria.registry import _REVENUE_CAGR_YEARS
    from aristos_council.tools.screening import revenue_cagr

    revenue = [200.0, 170.0, 150.0, 120.0, 100.0]   # > window + 1 clean points
    pe = 25.0
    f = _fund("GARP", total_revenue=revenue, pe_ratio=pe)

    cagr_obs = _crit("min_revenue_cagr", f, 0.0).observed
    peg_obs = _crit("max_peg_ratio", f, 100.0).observed

    cagr_ref, _ = revenue_cagr(revenue, _REVENUE_CAGR_YEARS)   # independent calc
    assert abs(cagr_obs - cagr_ref) < 1e-12                    # same window
    # PEG denominator is exactly that same CAGR (PEG = PE / (CAGR x 100))
    assert abs(peg_obs - pe / (cagr_ref * 100.0)) < 1e-12


def test_growth_v1_screen_end_to_end():
    """Load growth_v1 and screen a GARP-quality fixture through run_screen."""
    from pathlib import Path

    from aristos_council.strategy.loader import load_strategy

    strat = load_strategy(
        Path(__file__).resolve().parents[1] / "strategies" / "growth_v1.yaml")
    garp = _fund("GARP", market_cap=5e10, pe_ratio=25.0,
                 total_revenue=[146.0, 121.0, 110.0, 100.0],     # ~13.5% CAGR
                 operating_income=[30000.0], tax_provision=[6000.0],
                 pretax_income=[25000.0], invested_capital=[120000.0])
    result = run_screen(
        strat.criteria,
        Evidence(fundamentals=garp, dividends=[], last_close=200.0),
        ticker="GARP",
    )
    assert [c.name for c in result.criteria] == [
        "min_revenue_cagr", "min_roic", "max_peg_ratio", "min_market_cap"]
    assert all(c.passed for c in result.criteria)   # GARP-quality passes all
    assert result.flags == []
