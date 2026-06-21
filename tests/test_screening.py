"""Unit tests for deterministic screening tools.

These are the most important tests in Phase 1: they pin the behaviour of the
ONLY place math is allowed to happen. The themes are deliberate:
- pass / fail / unverifiable are three distinct outcomes,
- missing data never silently becomes a passing zero,
- the dividend-growth streak is honest about what short history can prove.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DividendEvent, Fundamentals
from aristos_council.data.yfinance_adapter import _dividend_per_share, _payout_ratio
from aristos_council.tools.screening import (
    consecutive_dividend_growth_years,
    max_payout_criterion,
    min_growth_streak_criterion,
    min_market_cap_criterion,
    min_yield_criterion,
    nopat_roic,
    peg_ratio,
    revenue_cagr,
    run_strategy_screen,
    through_cycle_roic,
)


def _fund(**kw) -> Fundamentals:
    base = dict(ticker="TEST")
    base.update(kw)
    return Fundamentals(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# min_yield — derived from DPS / price; provider field ignored (units bug
# found live by the Critic on the NVDA run: percent-form 0.5 passed a
# decimal threshold of 0.025)
# --------------------------------------------------------------------------- #
def test_yield_derived_pass():
    r = min_yield_criterion(_fund(dividend_per_share=4.0),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is True
    assert abs(r.observed - 0.04) < 1e-12
    assert "derived" in r.note


def test_yield_derived_fail_nvda_case():
    # NVDA-like: $1.00 DPS on a $200 stock = 0.5% — must FAIL a 2.5% floor.
    # The old provider-field comparison wrongly passed this.
    r = min_yield_criterion(_fund(dividend_per_share=1.0,
                                  dividend_yield=0.5),  # ambiguous field present
                            min_yield=0.025, last_close=200.0)
    assert r.passed is False
    assert abs(r.observed - 0.005) < 1e-12


def test_yield_provider_field_alone_is_unverifiable():
    # A PAYING company (dps present) but no price: we refuse to trust the
    # provider yield field, so the criterion is unverifiable — NOT a no-dividend
    # FAIL (that path needs dps zero/null).
    r = min_yield_criterion(_fund(dividend_per_share=2.0, dividend_yield=2.54),
                            min_yield=0.025)
    assert r.passed is None
    assert "ambiguous units" in r.note


def test_yield_boundary_is_inclusive():
    r = min_yield_criterion(_fund(dividend_per_share=2.5),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is True


# --------------------------------------------------------------------------- #
# max_payout
# --------------------------------------------------------------------------- #
# These exercise the payout logic itself, so they need a CURRENT dividend
# present (dps > 0); without one the criterion short-circuits to NOT-EVAL (see
# the no-current-dividend tests below).
def test_payout_pass():
    r = max_payout_criterion(_fund(payout_ratio=0.5, dividend_per_share=2.0),
                             max_payout=0.75)
    assert r.passed is True


def test_payout_fail_over_ceiling():
    r = max_payout_criterion(_fund(payout_ratio=0.9, dividend_per_share=2.0),
                             max_payout=0.75)
    assert r.passed is False


def test_negative_payout_is_fail_not_unverifiable():
    r = max_payout_criterion(_fund(payout_ratio=-0.2, dividend_per_share=2.0),
                             max_payout=0.75)
    assert r.passed is False
    assert "negative" in r.note


def test_payout_unverifiable_when_missing():
    # Paying company, but the provider didn't supply the ratio: unverifiable.
    r = max_payout_criterion(_fund(payout_ratio=None, dividend_per_share=2.0),
                             max_payout=0.75)
    assert r.passed is None


# --------------------------------------------------------------------------- #
# NULL vs ZERO dividend_per_share are DIFFERENT outcomes (hard rule 3):
#   - ZERO (genuine non-payer, e.g. a suspended dividend): yield FAILs
#     ("no current dividend"); payout NOT-EVAL (nothing to sustain — a
#     0.0:PASS would mislead).
#   - NULL (data gap, figure unavailable): BOTH NOT-EVAL. A missing figure must
#     NEVER become a phantom FAIL (the yfinance dividendRate-absent live bug).
# --------------------------------------------------------------------------- #
def test_genuine_zero_dividend_yield_fails():
    r = min_yield_criterion(_fund(dividend_per_share=0.0),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is False                 # evaluated-and-failed, not None
    assert r.observed == 0.0
    assert "no current dividend" in r.note


def test_null_dividend_figure_yield_is_not_eval_not_fail():
    # The phantom-FAIL bug: a MISSING figure must be NOT-EVAL, never a FAIL.
    r = min_yield_criterion(_fund(dividend_per_share=None),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is None                  # NOT-EVAL, not False
    assert r.observed is None                # no fabricated 0.0
    assert "unavailable" in r.note and "data gap" in r.note


def test_genuine_zero_dividend_payout_not_evaluated():
    # A reported 0.0 payout must NOT pass — it would read as "sustainable".
    r = max_payout_criterion(_fund(dividend_per_share=0.0, payout_ratio=0.0),
                             max_payout=0.75)
    assert r.passed is None                  # NOT-EVAL, not a misleading PASS
    assert "no current dividend" in r.note


def test_null_dividend_figure_payout_is_not_eval_with_data_gap_note():
    r = max_payout_criterion(_fund(dividend_per_share=None, payout_ratio=0.0),
                             max_payout=0.75)
    assert r.passed is None
    assert "unavailable" in r.note           # data-gap note, distinct from zero


# --------------------------------------------------------------------------- #
# market cap
# --------------------------------------------------------------------------- #
def test_market_cap_pass():
    r = min_market_cap_criterion(_fund(market_cap=2e10), min_market_cap=1e10)
    assert r.passed is True


def test_market_cap_fail():
    r = min_market_cap_criterion(_fund(market_cap=5e9), min_market_cap=1e10)
    assert r.passed is False


# --- Foreign-listing currency safety (honest abstention, no FX) ----------- #
def test_market_cap_not_eval_for_non_usd_listing_sk_hynix():
    # SK Hynix (000660.KS): 1.69e15 KRW would PASS a 1e10 USD floor for the
    # WRONG reason. A non-USD listing must ABSTAIN (NOT-EVAL), not silently pass.
    r = min_market_cap_criterion(
        _fund(market_cap=1.69e15, currency="KRW"), min_market_cap=1e10)
    assert r.passed is None                  # NOT-EVAL, not a (false) PASS
    assert r.observed is None
    assert "KRW" in r.note and "not USD" in r.note and "no fx" in r.note.lower()


def test_market_cap_usd_currency_unchanged():
    # An explicit USD currency evaluates exactly as before (no abstention).
    r = min_market_cap_criterion(
        _fund(market_cap=2e10, currency="USD"), min_market_cap=1e10)
    assert r.passed is True


def test_market_cap_missing_currency_evaluates_normally():
    # No currency reported (the pre-currency-field case) must NOT abstain —
    # otherwise every USD record predating the field would turn NOT-EVAL.
    r = min_market_cap_criterion(_fund(market_cap=2e10), min_market_cap=1e10)
    assert r.passed is True


def test_dividend_yield_is_currency_invariant():
    # Yield = dps/last_close is a dimensionless ratio: a KRW payer evaluates
    # normally (we must NOT over-abstain on currency-invariant criteria).
    r = min_yield_criterion(
        _fund(dividend_per_share=68_640.0, currency="KRW"),   # 3% of 2.288M KRW
        min_yield=0.025, last_close=2_288_000.0)
    assert r.passed is True
    assert abs(r.observed - 0.03) < 1e-9


# --------------------------------------------------------------------------- #
# dividend growth streak
# --------------------------------------------------------------------------- #
def _annual_divs(start_year: int, amounts: list[float]) -> list[DividendEvent]:
    """One dividend per year, ascending years."""
    return [
        DividendEvent(ex_date=date(start_year + i, 6, 1), amount=a)
        for i, a in enumerate(amounts)
    ]


def test_streak_counts_strictly_increasing_complete_years():
    # 2018..2023 amounts increasing; latest (2023) dropped as possibly-partial,
    # so complete years are 2018..2022 -> 4 consecutive increases.
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    streak, note = consecutive_dividend_growth_years(divs)
    assert streak == 4
    assert "floor" in note


def test_streak_breaks_on_cut():
    # Years: 2018=1.0 2019=1.1 2020=1.2 2021=0.9(CUT) 2022=1.0 2023=1.1
    # Latest year 2023 is dropped as possibly-partial -> complete years end 2022.
    # Walk back: 2022(1.0)>2021(0.9) up (streak 1); 2021(0.9)>2020(1.2)? no -> stop.
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 0.9, 1.0, 1.1])
    streak, _ = consecutive_dividend_growth_years(divs)
    assert streak == 1


def test_streak_none_when_no_dividends():
    streak, note = consecutive_dividend_growth_years([])
    assert streak is None
    assert "no dividend" in note


def test_streak_none_when_too_short():
    streak, note = consecutive_dividend_growth_years(_annual_divs(2022, [1.0]))
    assert streak is None
    assert "insufficient" in note


def test_growth_criterion_unverifiable_propagates():
    r = min_growth_streak_criterion([], min_years=25)
    assert r.passed is None


def test_growth_criterion_fail_when_streak_too_short():
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5])  # streak 4
    r = min_growth_streak_criterion(divs, min_years=25)
    assert r.passed is False
    assert r.observed == 4.0


# --- Per-payment-rate counting: immune to ex-date timing (hard-rule-5 fix) -- #
def _quarterly(start_year, n_years, base_rate, step, extra_exdate_year=None):
    """n_years of 4 quarterly payments at a per-payment rate rising `step`/yr;
    optionally an EXTRA 5th ex-date in `extra_exdate_year` — the calendar-boundary
    artifact (an extra payment landing in one calendar year, as PG's 2002 did)."""
    evs = []
    for y in range(n_years):
        year = start_year + y
        rate = base_rate + step * y
        for m in (2, 5, 8, 11):
            evs.append(DividendEvent(ex_date=date(year, m, 1), amount=rate))
        if year == extra_exdate_year:
            evs.append(DividendEvent(ex_date=date(year, 12, 15), amount=rate))
    return evs


def test_calendar_boundary_extra_exdate_does_not_false_break():
    # PG-2002 shape: 30 rising years, ONE year gets a 5th ex-date. The
    # per-payment-rate method must NOT read the next year as a cut.
    evs = _quarterly(1990, 30, 0.20, 0.01, extra_exdate_year=2002)
    streak, note = consecutive_dividend_growth_years(evs)
    assert streak >= 25                          # clears the aristocrat threshold
    assert streak == 28                          # full count (30y, latest dropped)
    assert "per-payment" in note and "floor" in note
    # the artifact year's SUM really does exceed the next year's, so the OLD
    # calendar-year-sum method WOULD have false-broken here:
    sum_2002 = sum(e.amount for e in evs if e.ex_date.year == 2002)
    sum_2003 = sum(e.amount for e in evs if e.ex_date.year == 2003)
    assert sum_2002 > sum_2003


def test_genuine_per_payment_cut_still_breaks():
    # T-shape: rising for years, then a REAL per-payment cut to a lower flat
    # rate. The drop must break the streak — the fix must not paper over cuts.
    rising = _quarterly(2000, 22, 0.40, 0.01)               # 2000..2021 rising
    cut = _quarterly(2022, 4, 0.2775, 0.0)                  # 2022..2025 flat, lower
    streak, _ = consecutive_dividend_growth_years(rising + cut)
    # latest (2025) dropped; 2024 vs 2023 both 0.2775 -> not increasing -> break
    assert streak == 0


def test_single_year_real_decrease_breaks_at_the_cut():
    # A genuine one-year per-payment decrease mid-history breaks the count there,
    # independent of ex-date counts (rate, not sum).
    evs = (_quarterly(2010, 5, 0.50, 0.05)                  # 2010..2014 rising
           + _quarterly(2015, 4, 0.40, 0.05))              # 2015 CUT then rising
    streak, _ = consecutive_dividend_growth_years(evs)
    # complete years end 2017 (2018 dropped). 2017>2016>2015 up; 2015(0.40)
    # < 2014(0.70) -> break. Two increases (2016,2017) since the cut.
    assert streak == 2


# --------------------------------------------------------------------------- #
# aggregate screen
# --------------------------------------------------------------------------- #
def test_screen_flags_unverifiable_streak():
    f = _fund(dividend_per_share=4.0, payout_ratio=0.5, market_cap=2e10)
    result = run_strategy_screen(
        f,
        dividends=[],  # forces streak unverifiable
        min_yield=0.025,
        max_payout=0.75,
        min_market_cap=1e10,
        min_growth_years=25,
        last_close=100.0,
    )
    assert any("unverifiable:min_dividend_growth_streak" in fl for fl in result.flags)
    # The three quantified criteria evaluated and passed...
    assert all(c.passed for c in result.evaluated)
    # ...but the screen is NOT a clean all-pass because the streak is unproven.
    assert len(result.unverifiable) == 1


def _screen(fund, dividends):
    return run_strategy_screen(
        fund, dividends=dividends,
        min_yield=0.025, max_payout=0.75, min_market_cap=1e10,
        min_growth_years=25, last_close=100.0,
    )


def _criterion(result, name):
    return next(c for c in result.criteria if c.name == name)


def test_genuine_zero_no_history_brkb_amzn_arm():
    # Genuine non-payer (explicit ZERO current DPS) AND no dividend history.
    f = _fund(dividend_per_share=0.0, payout_ratio=0.0, market_cap=2e10)
    result = _screen(f, dividends=[])
    yld = _criterion(result, "min_dividend_yield")
    pay = _criterion(result, "max_payout_ratio")
    assert yld.passed is False and "no current dividend" in yld.note
    assert pay.passed is None and "no current dividend" in pay.note


def test_null_dividend_figure_is_not_eval_regardless_of_history():
    # Figure unavailable (data gap): yield NOT-EVAL (NOT a phantom FAIL) and
    # payout NOT-EVAL — independent of dividend history.
    f = _fund(dividend_per_share=None, payout_ratio=0.0, market_cap=2e10)
    result = _screen(f, dividends=[])
    yld = _criterion(result, "min_dividend_yield")
    pay = _criterion(result, "max_payout_ratio")
    assert yld.passed is None and yld.observed is None
    assert pay.passed is None


def test_suspended_dividend_intc_genuine_zero_fails():
    # Suspended payer (INTC-shape): explicit ZERO current DPS but a long PAST
    # history (128 quarterly events). Genuine non-payer today -> yield FAILs,
    # payout NOT-EVAL. The determination depends on current DPS, not history.
    past = [
        DividendEvent(ex_date=date(1992 + i // 4, 3 * (i % 4) + 1, 1),
                      amount=0.10 + 0.001 * i)
        for i in range(128)
    ]
    f = _fund(dividend_per_share=0.0, payout_ratio=0.0, market_cap=2e10)
    result = _screen(f, dividends=past)
    yld = _criterion(result, "min_dividend_yield")
    pay = _criterion(result, "max_payout_ratio")
    assert yld.passed is False and "no current dividend" in yld.note
    assert pay.passed is None and "no current dividend" in pay.note


def test_screen_passes_all_evaluated_with_full_data():
    f = _fund(dividend_per_share=4.0, payout_ratio=0.5, market_cap=2e10)
    divs = _annual_divs(1990, [1.0 + 0.1 * i for i in range(33)])  # long streak
    result = run_strategy_screen(
        f,
        dividends=divs,
        min_yield=0.025,
        max_payout=0.75,
        min_market_cap=1e10,
        min_growth_years=25,
        last_close=100.0,
    )
    assert result.flags == []
    assert result.passes_all_evaluated is True


# --------------------------------------------------------------------------- #
# Adapter dividend_per_share fallback (PART B): yfinance's forward dividendRate
# is often absent for genuine payers (PG/JNJ/MO/T/MMM observed None in one call
# while KO/MSFT/ASML were populated); fall back to trailingAnnualDividendRate.
# --------------------------------------------------------------------------- #
def test_dps_falls_back_to_trailing_when_forward_missing():
    # JNJ/PG/MO/T/MMM shape: dividendRate absent, trailing populated -> recover.
    assert _dividend_per_share({"trailingAnnualDividendRate": 5.2}) == 5.2
    assert _dividend_per_share(
        {"dividendRate": None, "trailingAnnualDividendRate": 4.227}) == 4.227


def test_dps_prefers_forward_when_present():
    # KO/MSFT/ASML shape: forward present -> used as-is, NO fallback (unchanged).
    assert _dividend_per_share(
        {"dividendRate": 2.12, "trailingAnnualDividendRate": 2.06}) == 2.12
    assert _dividend_per_share({"dividendRate": 3.64}) == 3.64


def test_dps_intc_suspension_is_zero_not_none():
    # INTC shape: forward absent, trailing explicit 0 -> 0.0 (genuine non-payer,
    # the screen FAILs it), NOT None (which would NOT-EVAL).
    assert _dividend_per_share(
        {"dividendRate": None, "trailingAnnualDividendRate": 0}) == 0.0


def test_dps_none_when_both_absent():
    # No figure anywhere -> None (screen NOT-EVALs, never phantom-FAILs).
    assert _dividend_per_share({}) is None
    assert _dividend_per_share(
        {"dividendRate": None, "trailingAnnualDividendRate": None}) is None


def test_recovered_dps_yields_a_real_value_end_to_end():
    # JNJ-shape: dividendRate None recovered to 5.2 via trailing; at a ~$155
    # close the derived yield is a real ~3.4%, evaluated normally (not a FAIL).
    dps = _dividend_per_share({"dividendRate": None,
                               "trailingAnnualDividendRate": 5.2})
    r = min_yield_criterion(_fund(dividend_per_share=dps),
                            min_yield=0.025, last_close=155.0)
    assert r.passed is True
    assert abs(r.observed - 5.2 / 155.0) < 1e-9


# --------------------------------------------------------------------------- #
# Adapter payout_ratio fallback (same summaryDetail gap): derive from
# dividend_per_share / trailingEps when the provider payoutRatio is None.
# --------------------------------------------------------------------------- #
def test_payout_falls_back_to_dps_over_eps_when_provider_missing():
    # JNJ-shape: payoutRatio absent, dps recovered + eps present -> derive.
    pr = _payout_ratio({"payoutRatio": None, "trailingEps": 8.63},
                       dividend_per_share=5.2)
    assert abs(pr - 5.2 / 8.63) < 1e-9                  # ~0.60


def test_payout_prefers_provider_field_when_present():
    # KO/MSFT/ASML shape: provider payoutRatio present -> used as-is, NO derive.
    assert _payout_ratio({"payoutRatio": 0.6478, "trailingEps": 3.18},
                         dividend_per_share=2.12) == 0.6478


def test_payout_not_eval_when_eps_nonpositive_or_missing():
    # Can't compute honestly: no eps, or non-positive eps (negative earnings).
    assert _payout_ratio({"payoutRatio": None}, dividend_per_share=5.2) is None
    assert _payout_ratio({"payoutRatio": None, "trailingEps": -0.6},
                         dividend_per_share=5.2) is None      # INTC-like neg eps
    assert _payout_ratio({"payoutRatio": None, "trailingEps": 0.0},
                         dividend_per_share=5.2) is None


def test_payout_not_eval_when_dps_missing():
    # No dividend figure to compute from -> NOT-EVAL (never a phantom value).
    assert _payout_ratio({"payoutRatio": None, "trailingEps": 8.63},
                         dividend_per_share=None) is None


def test_derived_payout_evaluates_in_criterion_end_to_end():
    # JNJ-shape through the criterion: provider field absent but derived payout
    # 0.60 <= 0.75 ceiling -> PASS (was NOT-EVAL before this fix).
    pr = _payout_ratio({"payoutRatio": None, "trailingEps": 8.63},
                       dividend_per_share=5.2)
    r = max_payout_criterion(_fund(dividend_per_share=5.2, payout_ratio=pr),
                             max_payout=0.75)
    assert r.passed is True
    assert abs(r.observed - 5.2 / 8.63) < 1e-9


# --------------------------------------------------------------------------- #
# Growth / quality primitives (Sprint 4B) — pure math + NOT-EVAL edges
# --------------------------------------------------------------------------- #
def test_revenue_cagr_basic():
    # Monotonic series: the log-linear TREND CAGR ~= the two-point endpoint CAGR
    # (~0.1346), so they barely diverge -> NO dispersion warning.
    cagr, note = revenue_cagr([146.0, 121.0, 110.0, 100.0], 3)
    assert abs(cagr - 0.1310) < 1e-3            # trend, not the 0.1346 endpoint
    assert "WARNING" not in note
    cagr2, _ = revenue_cagr([110.0, 100.0], 1)   # 1-year: 2-point trend == endpoint
    assert abs(cagr2 - 0.10) < 1e-9


def test_revenue_cagr_trough_base_trend_below_endpoint_with_warning():
    # SK Hynix-shape: a deep cyclical-trough BASE year inflates the two-point
    # endpoint CAGR; the trend (all points) sees through it and is MEANINGFULLY
    # lower, AND the note WARNS that the base year may be cyclical.
    rev = [100.0, 95.0, 90.0, 12.0]            # newest-first; oldest 12 = trough
    trend, note = revenue_cagr(rev, 3)
    endpoint = (rev[0] / rev[3]) ** (1.0 / 3) - 1.0   # ~1.03 (inflated)
    assert trend is not None
    assert trend < endpoint - 0.10             # meaningfully lower
    assert "WARNING" in note and "cyclical" in note


def test_revenue_cagr_not_eval_short_or_nonpositive():
    assert revenue_cagr([120.0, 100.0], 3)[0] is None        # too few points
    assert revenue_cagr([130.0, 110.0, 100.0, 0.0], 3)[0] is None    # base 0
    assert revenue_cagr([130.0, 110.0, 100.0, -5.0], 3)[0] is None   # base <0
    assert revenue_cagr([130.0, -1.0, 100.0, 90.0], 3)[0] is None    # mid non-positive


def test_nopat_roic_basic_and_tax():
    # op 100, eff tax = 20/100 = 0.2 -> NOPAT 80 -> ROIC 80/400 = 0.20
    roic, note = nopat_roic(100.0, 20.0, 100.0, 400.0)
    assert abs(roic - 0.20) < 1e-9
    assert "effective tax rate" in note


def test_nopat_roic_negative_nopat_is_a_value_not_an_error():
    roic, _ = nopat_roic(-50.0, 0.0, -60.0, 400.0)   # negative op income
    assert roic is not None and roic < 0


def test_nopat_roic_not_eval_on_missing_invested_capital():
    assert nopat_roic(100.0, 20.0, 100.0, None)[0] is None
    assert nopat_roic(100.0, 20.0, 100.0, 0.0)[0] is None
    assert nopat_roic(None, 20.0, 100.0, 400.0)[0] is None  # missing op income


def test_nopat_roic_falls_back_when_tax_unusable():
    # no usable pretax -> eff tax 0 -> NOPAT = op income
    roic, note = nopat_roic(100.0, None, None, 400.0)
    assert abs(roic - 0.25) < 1e-9
    assert "assumed 0" in note


def test_peg_ratio_basic_and_undefined():
    peg, _ = peg_ratio(25.0, 0.1346)
    assert abs(peg - 1.857) < 1e-2
    assert peg_ratio(None, 0.10)[0] is None      # no PE
    assert peg_ratio(-5.0, 0.10)[0] is None      # negative PE
    assert peg_ratio(25.0, 0.0)[0] is None       # zero growth
    assert peg_ratio(25.0, -0.05)[0] is None     # negative growth


def test_peg_ratio_winsorizes_extreme_growth():
    # An extreme (trough-inflated) CAGR is capped at 0.40 before forming PEG, so
    # PEG is LARGER (more conservative) than the un-winsorized value, and the note
    # records the clamp.
    peg, note = peg_ratio(25.0, 0.80)            # raw CAGR 80% -> winsor to 40%
    assert abs(peg - 25.0 / (0.40 * 100.0)) < 1e-9   # 0.625, uses the cap
    assert peg > 25.0 / (0.80 * 100.0)               # > the un-winsorized 0.3125
    assert "winsorized" in note and "0.80" in note


def test_through_cycle_roic_below_peak_based():
    # Peak LATEST operating income with a NEGATIVE prior year: the through-cycle
    # mean is far below the peak, so through-cycle ROIC is MEANINGFULLY lower than
    # the peak-(latest-)based ROIC, and the note says through-cycle.
    oi = [100.0, -20.0, 30.0, 40.0]              # newest-first; latest 100 is a peak
    tax = [10.0, 0.0, 3.0, 4.0]
    pretax = [90.0, -25.0, 28.0, 38.0]
    ic = [400.0]
    roic_tc, note = through_cycle_roic(oi, tax, pretax, ic, window=4)
    peak_roic, _ = nopat_roic(oi[0], tax[0], pretax[0], ic[0])   # latest-only
    assert roic_tc is not None and roic_tc < peak_roic - 0.05    # meaningfully lower
    assert "through-cycle" in note


def test_through_cycle_roic_single_point_falls_back():
    roic, note = through_cycle_roic([12000.0], [2500.0], [10000.0], [30000.0],
                                    window=4)
    assert roic is not None
    assert "single-period" in note               # flagged: no cycle history


def test_through_cycle_roic_not_eval_on_missing_inputs():
    assert through_cycle_roic([], [1.0], [2.0], [400.0], window=4)[0] is None  # no OI
    assert through_cycle_roic([100.0], [10.0], [90.0], [], window=4)[0] is None  # no IC
