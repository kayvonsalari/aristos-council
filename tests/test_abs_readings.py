"""ABS-READINGS-1 — debt-and-cash and the growth record, on fabricated statements.

These two readings exist because every lens in this repo is RELATIVE: a BUY means "near
the top of the list this ran on", which is no answer at all to "what is this company
like". They do not vote and no strategy selects them.

What is actually pinned here is the abstention contract, because that is where a reading
like this goes wrong. A company with no interest bill does not have infinite cover. A
company whose earnings were negative ten years ago does not have a ten-year compound
growth rate. A missing cash balance is not a zero cash balance. Each of those is a
sentence, not a number, and each has a test.
"""
from __future__ import annotations

import pytest

from aristos_council.abs_readings import (GROWTH_WINDOWS, MIN_GROWTH_YEARS, Reading,
                                          debt_and_cash, growth_record)
from aristos_council.data.adapter import Fundamentals


def _f(**kw) -> Fundamentals:
    aligned = kw.pop("aligned", None)
    base = dict(ticker="X", currency="USD", financial_currency="USD")
    base.update(kw)
    if aligned:
        base["aligned_annual"] = aligned
    return Fundamentals(**base)


# =========================================================================== #
# A. debt and cash
# =========================================================================== #
def test_net_debt_is_debt_minus_cash_and_reads_as_a_sentence():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9))
    assert out.net_debt.value == pytest.approx(40e9)
    assert out.net_debt.label == "owes $40.0bn net of cash"


def test_more_cash_than_debt_is_said_the_other_way_round():
    out = debt_and_cash(_f(total_debt=5e9, total_cash=20e9))
    assert out.net_debt.value == pytest.approx(-15e9)
    assert "holds $15.0bn more cash than debt" in out.net_debt.label


def test_a_MISSING_cash_balance_is_disclosed_not_treated_as_zero():
    """house rule 3 on the balance sheet: an absent value is not a zero one. The figure is
    still given — it is gross debt — and it says which it is."""
    out = debt_and_cash(_f(total_debt=50e9))
    assert out.net_debt.value == pytest.approx(50e9)
    assert "cash not reported, so this is gross debt" in out.net_debt.label


def test_years_of_cash_flow_to_repay_the_debt():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9, operating_cash_flow=8e9))
    assert out.net_debt_to_ocf.value == pytest.approx(5.0)
    assert out.net_debt_to_ocf.label == (
        "would take 5.0 years of operating cash flow to repay its debt")


def test_negative_operating_cash_flow_abstains_and_says_why():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=0.0, operating_cash_flow=-2e9))
    assert not out.net_debt_to_ocf.available
    assert "not positive" in out.net_debt_to_ocf.note
    assert "no number of years" in out.net_debt_to_ocf.note


def test_a_company_with_no_net_debt_has_nothing_to_repay():
    out = debt_and_cash(_f(total_debt=1e9, total_cash=9e9, operating_cash_flow=2e9,
                           free_cash_flow=1e9))
    assert out.net_debt_to_ocf.label == "has no net debt to repay"
    assert out.years_to_repay.label == "has no net debt to repay"


def test_interest_cover_is_operating_income_over_the_interest_bill():
    out = debt_and_cash(_f(operating_income=12e9,
                           aligned={"interest_expense": [1.5e9]}))
    assert out.interest_cover.value == pytest.approx(8.0)
    assert out.interest_cover.label == "earns 8.0 times its interest bill"


def test_a_negative_interest_line_is_read_as_its_size():
    """Providers sign the interest line either way; the bill is its magnitude."""
    out = debt_and_cash(_f(operating_income=12e9,
                           aligned={"interest_expense": [-1.5e9]}))
    assert out.interest_cover.value == pytest.approx(8.0)


def test_ZERO_interest_expense_abstains_rather_than_reporting_infinity():
    """A company with no interest bill does not have infinite cover — it has no ratio, and
    a huge number would read as a strength measured on the same scale as a real one."""
    out = debt_and_cash(_f(operating_income=12e9, aligned={"interest_expense": [0.0]}))
    assert not out.interest_cover.available
    assert "no interest expense" in out.interest_cover.note
    assert "inf" not in out.interest_cover.text().lower()


def test_a_MISSING_interest_expense_abstains_differently_from_a_zero_one():
    out = debt_and_cash(_f(operating_income=12e9))
    assert not out.interest_cover.available
    assert "not reported" in out.interest_cover.note


def test_years_to_repay_from_free_cash_flow():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9, free_cash_flow=5e9))
    assert out.years_to_repay.value == pytest.approx(8.0)
    assert "8.0 years of free cash flow" in out.years_to_repay.label


def test_non_positive_free_cash_flow_abstains_and_says_what_that_means():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=0.0, free_cash_flow=-1e9))
    assert not out.years_to_repay.available
    assert "not being repaid out of it at all" in out.years_to_repay.note


def test_no_fundamentals_at_all_abstains_on_every_leg():
    out = debt_and_cash(None)
    assert all(not r.available for r in (out.net_debt, out.net_debt_to_ocf,
                                         out.interest_cover, out.years_to_repay))
    assert all("no fundamentals" in line for line in out.lines())


def test_every_abstention_reads_as_a_sentence_not_a_blank():
    for line in debt_and_cash(_f()).lines():
        assert line.startswith("not stated — ") and len(line) > 20


# =========================================================================== #
# B. the growth record
# =========================================================================== #
def _revenue(values):
    return _f(aligned={"total_revenue": list(values)})


def test_a_compound_revenue_rate_over_five_years():
    # newest-first: 10 years of doubling every 5
    out = growth_record(_revenue([200.0, 180, 160, 140, 120, 100.0]))
    five = out.revenue.cagr[5]
    assert five.value == pytest.approx(0.1487, abs=1e-3)      # 200/100 over 5y
    assert "over 5 years" in five.label


def test_it_uses_the_years_that_exist_and_SAYS_how_many():
    out = growth_record(_revenue([150.0, 130, 110, 100.0]))   # 4 points = 3 years
    ten = out.revenue.cagr[10]
    assert ten.available
    assert "only 3 of 10 years available" in ten.label


def test_below_three_years_it_abstains():
    out = growth_record(_revenue([120.0, 100.0]))
    assert MIN_GROWTH_YEARS == 3
    for window in GROWTH_WINDOWS:
        assert not out.revenue.cagr[window].available
        assert "at least 3" in out.revenue.cagr[window].note


def test_a_non_positive_start_abstains_rather_than_rooting_a_negative():
    """A rate off a negative base is not a growth rate — the same discipline the PEG and
    revenue-CAGR criteria already use."""
    out = growth_record(_f(aligned={"diluted_eps": [3.0, 2.0, 1.0, -1.0, -2.0, -3.0]}))
    five = out.eps.cagr[5]
    assert not five.available
    assert "was not positive 5 years ago" in five.note


def test_grew_in_n_of_the_years_reported():
    out = growth_record(_revenue([150.0, 140, 120, 130, 110, 100.0]))
    # pairs newest-first: 150>140, 140>120, 120<130, 130>110, 110>100  -> 4 of 5
    assert out.revenue.grew_in.value == 4.0
    assert out.revenue.grew_in.label == "revenue grew in 4 of the 5 years reported"


def test_the_growth_count_states_the_years_REPORTED_not_a_flat_ten():
    out = growth_record(_revenue([120.0, 110, 100.0]))
    assert "of the 2 years reported" in out.revenue.grew_in.label


def test_holes_in_the_series_are_dropped_and_the_span_says_so():
    """A company with 2018 and 2024 but nothing between has six years of span on two
    points; the label reports the span it actually spanned."""
    out = growth_record(_revenue([200.0, None, None, 150.0, None, 100.0]))
    five = out.revenue.cagr[5]
    assert five.available
    assert "over 2 years" in five.label          # three present points -> a 2-year span


def test_eps_is_derived_from_net_income_and_shares_when_the_line_is_missing_AND_SAYS_SO():
    out = growth_record(_f(aligned={
        "net_income": [200.0, 180, 160, 140, 120, 100.0],
        "shares_outstanding": [100.0, 100, 100, 100, 100, 100.0]}))
    five = out.eps.cagr[5]
    assert five.available
    # said ONCE, as a note under the section - not on the three lines it used to be printed on
    assert "derived" not in five.label
    assert not any("derived" in line for line in out.lines())
    assert out.notes().count("earnings per share is derived from net income and share count") == 1


# =========================================================================== #
# GROWTH-SANITY-1 (Batch 15 item 3a) — a base-year caution, like VALBAND-1's own
# sanity bound, but it never withholds the figure: the rate is shown, with a visible
# caveat, when it looks like a trough-base artefact rather than a durable trend.
# Reference case: EL.PA, 2026-10-03 — EPS +90.9%/yr over 5 years vs +3.5%/yr over 10.
# =========================================================================== #
def test_an_extreme_rate_carries_the_caution():
    # end=100, start(5y)=3.125 -> rate = (100/3.125)^(1/5)-1 = 100%/yr, well past 50%.
    out = growth_record(_f(aligned={"diluted_eps": [100.0, 50, 25, 12.5, 6.25, 3.125]}))
    five = out.eps.cagr[5]
    assert five.value == pytest.approx(1.0, abs=1e-3)
    assert "CAUTION: starting year unusually low" in five.text()
    assert "CAUTION" in five.text()


def test_a_low_base_relative_to_the_series_median_carries_the_caution_even_under_50_pct():
    # end=70, start(5y)=10 -> rate ~47.6%/yr (under the 50% trigger on its own), but 10 is
    # far below the full 10-point series' own median of 50 (10 < 30% of 50).
    series = [70.0, 65, 60, 90, 85, 10, 40, 35, 30, 25]
    out = growth_record(_f(aligned={"diluted_eps": series}))
    five = out.eps.cagr[5]
    assert five.value == pytest.approx(0.4758, abs=1e-3)
    assert five.value < 0.50                              # confirms trigger 1 alone would NOT fire
    assert "CAUTION: starting year unusually low" in five.text()


def test_an_ordinary_steady_rate_carries_no_caution():
    out = growth_record(_revenue([130.0, 125, 120, 115, 110, 100.0]))
    five = out.revenue.cagr[5]
    assert five.value == pytest.approx(0.0539, abs=1e-3)
    assert five.caution == "" and "CAUTION" not in five.label and "CAUTION" not in five.text()


def test_the_caution_is_a_separate_field_never_mixed_into_the_bare_value():
    """The caution changes DISPLAY only — the underlying number a rank/criterion would
    read (``.value``) is untouched, matching 'math judges, LLM writes'."""
    out = growth_record(_f(aligned={"diluted_eps": [100.0, 50, 25, 12.5, 6.25, 3.125]}))
    five = out.eps.cagr[5]
    assert five.value == pytest.approx(1.0, abs=1e-3)      # the number itself: unaffected
    assert five.caution != "" and five.caution not in five.label  # caution is its OWN field


def test_the_caution_survives_the_five_and_ten_year_window_collapse():
    """ABS-READINGS-2's own window-collapse branch (lines() reconstructs ``head`` from
    ``label`` by splitting on " (only ") must not silently drop a caution appended after
    that split point."""
    # Only 4 points on file (3-year span) -> both the 5y and 10y windows collapse onto
    # the SAME 3-year reading, exercising lines()'s collapsed-span branch directly.
    series = [100.0, 50, 25, 12.5]
    out = growth_record(_f(aligned={"diluted_eps": series}))
    five = out.eps.cagr[5]
    assert five.caution and "CAUTION" in five.caution     # the underlying Reading has it
    assert any("CAUTION" in line for line in out.eps.lines())   # and so does the rendered line


def test_the_narrator_must_never_repeat_a_flagged_figure_without_the_caution():
    """COUNCIL-FIX-1(f)/GROWTH-SANITY-1(a) — the prompt instruction is pinned once here
    rather than only in test_prompts.py, so this fact and its narration-level guard are
    cross-referenced from the same place a reader would look for the caution's EFFECT."""
    from aristos_council.agents.prompts import HARD_RULES

    assert "GROWTH CAUTION" in HARD_RULES
    assert "(CAUTION: ...)" in HARD_RULES or "CAUTION:" in HARD_RULES


def test_the_accounts_currency_is_used_not_the_listings():
    """AstraZeneca is quoted in pence and reports in dollars: 'owes 27.4bn GBp net of cash' put the
    LISTING's currency on the ACCOUNTS' figure (same root cause as the NVO kroner-as-dollars bug)."""
    out = debt_and_cash(_f(currency="GBp", financial_currency="USD",
                           total_debt=30.0e9, total_cash=2.6e9))
    assert out.net_debt.label == "owes $27.4bn net of cash"
    assert "GBp" not in " ".join(out.lines())


def test_an_unstated_accounts_currency_says_so_and_is_never_guessed_from_the_listing():
    out = debt_and_cash(_f(currency="GBp", financial_currency=None,
                           total_debt=30.0e9, total_cash=2.6e9))
    assert out.net_debt.label == "owes 27.4bn (currency not stated by the source) net of cash"
    assert "GBp" not in out.net_debt.label and "$" not in out.net_debt.label


def test_money_uses_the_one_formatter_for_a_trillion_and_a_franc():
    assert "owes $1.03tn net of cash" in debt_and_cash(
        _f(total_debt=1.03e12, total_cash=0.0)).net_debt.label
    assert "owes CHF 5.2bn net of cash" in debt_and_cash(
        _f(financial_currency="CHF", total_debt=5.2e9, total_cash=0.0)).net_debt.label


def test_the_growth_notes_say_how_much_history_stands_behind_the_record_once():
    record = growth_record(_f(aligned={"total_revenue": [float(200 - 4 * i) for i in range(36)]}))
    assert record.years_on_file == 36
    assert record.notes()[0] == "36 years on file, last 10 used"
    short = growth_record(_f(aligned={"total_revenue": [150.0, 130, 110, 100.0]}))
    assert short.notes()[0] == "4 years on file, all used"


def test_the_reported_eps_line_is_preferred_over_the_derivation():
    out = growth_record(_f(aligned={
        "diluted_eps": [2.0, 1.8, 1.6, 1.4, 1.2, 1.0],
        "net_income": [999.0] * 6, "shares_outstanding": [1.0] * 6}))
    assert "derived" not in out.eps.cagr[5].label
    assert out.eps.cagr[5].value == pytest.approx(0.1487, abs=1e-3)


def test_no_earnings_data_at_all_abstains_on_the_eps_leg_only():
    out = growth_record(_revenue([150.0, 130, 110, 100.0]))
    assert out.revenue.cagr[5].available
    assert not out.eps.cagr[5].available


def test_the_record_counts_the_years_it_had():
    out = growth_record(_revenue([150.0, None, 130, 110, 100.0]))
    assert out.revenue.years_available == 4


# =========================================================================== #
# neither reading is a lens
# =========================================================================== #
def test_no_strategy_selects_either_reading():
    """They are facts on a page, not votes. If one ever became a factor it would change
    every ranking that selected it, and this is the line that would go red."""
    from pathlib import Path

    text = " ".join(p.read_text(encoding="utf-8")
                    for p in Path("strategies").glob("*.yaml"))
    assert "debt_and_cash" not in text
    assert "growth_record" not in text


def test_neither_reading_is_in_the_factor_registry():
    from aristos_council.factors import FACTOR_REGISTRY

    assert "debt_and_cash" not in FACTOR_REGISTRY
    assert "growth_record" not in FACTOR_REGISTRY


def test_a_reading_never_carries_both_a_value_and_an_abstention():
    for reading in (Reading(value=1.0, label="x"), Reading(note="why")):
        assert bool(reading.available) != bool(reading.note)

# =========================================================================== #
# ABS-READINGS-2 - five defects found by reading the page for KO, T, NVDA,
# LHA.DE and NFLX
# =========================================================================== #
from aristos_council.abs_readings import INTEREST_IMMATERIAL_ABOVE


def _years(n, start, step):
    """``n`` annual figures, NEWEST FIRST, compounding backwards from ``start``."""
    return [start * (step ** i) for i in range(n)]


# -- item 3: one line per distinct span ------------------------------------- #
def test_four_periods_print_ONE_growth_line_not_two_identical_ones():
    """With four annual periods both windows fall back to a 3-year span, and the page
    printed the identical sentence twice - once for the 5-year window and once for the
    10-year."""
    out = growth_record(_f(aligned={"total_revenue": [47e9, 45.8e9, 43e9, 38.7e9]}))
    lines = out.revenue.lines()
    cagr_lines = [l for l in lines if "compounded" in l]
    assert len(cagr_lines) == 1, cagr_lines


def test_that_one_line_states_the_span_used_and_BOTH_windows_it_could_not_fill():
    out = growth_record(_f(aligned={"total_revenue": [47e9, 45.8e9, 43e9, 38.7e9]}))
    line = next(l for l in out.revenue.lines() if "compounded" in l)
    assert "over 3 years" in line
    assert "only 3 years of accounts are available" in line
    assert "neither the 5- nor the 10-year window could be filled" in line


def test_two_DIFFERENT_spans_still_print_two_lines():
    """Seven periods fills the 5-year window and not the 10-year: two real answers, two
    lines. The collapse is only for the duplicate case."""
    out = growth_record(_f(aligned={"total_revenue": _years(7, 100.0, 0.9)}))
    cagr_lines = [l for l in out.revenue.lines() if "compounded" in l]
    assert len(cagr_lines) == 2


def test_the_READINGS_keep_their_own_detail_even_when_the_lines_collapse():
    """The collapse is a RENDERING decision. Anything reading the figures
    programmatically still gets one Reading per window, each with its span."""
    out = growth_record(_f(aligned={"total_revenue": [47e9, 45.8e9, 43e9, 38.7e9]}))
    assert set(out.revenue.cagr) == set(GROWTH_WINDOWS)
    assert all(out.revenue.cagr[w].span == 3 for w in GROWTH_WINDOWS)
    assert "only 3 of 10 years available" in out.revenue.cagr[10].label


def test_twelve_years_of_history_are_USED_not_truncated_to_three():
    """The defect this item exists for: Coca-Cola, a company with a century of accounts,
    read "compounded over 3 years" because yfinance returns four annual periods."""
    revenue = _years(12, 50e9, 0.95)          # newest 50bn, falling 5% a year backwards
    eps = _years(12, 3.0, 0.92)
    out = growth_record(_f(aligned={"total_revenue": revenue, "diluted_eps": eps}))

    assert out.revenue.cagr[5].span == 5
    assert out.revenue.cagr[10].span == 10
    assert out.eps.cagr[10].span == 10
    assert out.revenue.years_available == 12
    # ...and the growth count uses ten years, not three
    assert "grew in 10 of the 10 years reported" in out.revenue.grew_in.label


def test_the_grew_in_count_is_capped_at_ten_even_with_twelve_years_on_file():
    out = growth_record(_f(aligned={"total_revenue": _years(12, 50e9, 0.95)}))
    assert out.revenue.grew_in.value == 10.0


# -- item 4: interest cover above 50x --------------------------------------- #
def test_interest_cover_of_503_reads_as_immaterial():
    """NVIDIA. "Earns 503.4 times its interest bill" invites a comparison with a company
    at 60x as though that were a ranking. Both simply have no interest problem."""
    out = debt_and_cash(_f(operating_income=100e9,
                           aligned={"interest_expense": [198_649_000]}))
    assert out.interest_cover.label == (
        "interest is immaterial (covered more than 50 times over)")
    assert "503" not in out.interest_cover.label


def test_the_exact_cover_is_KEPT_on_the_reading():
    out = debt_and_cash(_f(operating_income=100e9,
                           aligned={"interest_expense": [198_649_000]}))
    assert out.interest_cover.value == pytest.approx(503.4, abs=0.1)
    assert out.interest_cover.available


@pytest.mark.parametrize("times", [10.0, 25.0, 50.0])
def test_cover_at_or_below_the_line_still_gives_the_number(times):
    out = debt_and_cash(_f(operating_income=times * 1e9,
                           aligned={"interest_expense": [1e9]}))
    assert f"{times:.1f} times its interest bill" in out.interest_cover.label
    assert INTEREST_IMMATERIAL_ABOVE == 50.0


def test_just_above_the_line_switches_wording():
    out = debt_and_cash(_f(operating_income=50.1e9, aligned={"interest_expense": [1e9]}))
    assert "immaterial" in out.interest_cover.label


# -- item 2: free cash flow larger than operating cash flow ----------------- #
def test_free_cash_flow_larger_than_operating_cash_flow_ABSTAINS():
    """Netflix, 2026-09-20: 0.7 years to repay from operating cash flow and 0.3 from free
    cash flow, on the same page. Free cash flow is operating cash flow minus capital
    spending, so it is never larger and the period from it is never shorter."""
    out = debt_and_cash(_f(total_debt=16_654_660_608, total_cash=9_127_910_400,
                           operating_cash_flow=10_149_273_000,
                           free_cash_flow=25_387_552_768))
    assert not out.years_to_repay.available
    assert out.years_to_repay.note == (
        "the reported free cash flow is larger than operating cash flow, so the two "
        "figures disagree and neither is used here")


def test_the_operating_cash_flow_reading_is_UNAFFECTED_by_the_disagreement():
    """Only the figure that cannot be trusted abstains. The other one is still reported."""
    out = debt_and_cash(_f(total_debt=16_654_660_608, total_cash=9_127_910_400,
                           operating_cash_flow=10_149_273_000,
                           free_cash_flow=25_387_552_768))
    assert out.net_debt_to_ocf.available
    assert "0.7 years of operating cash flow" in out.net_debt_to_ocf.label


def test_a_consistent_pair_is_still_reported():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9,
                           operating_cash_flow=10e9, free_cash_flow=8e9))
    assert out.years_to_repay.available
    assert "5.0 years of free cash flow" in out.years_to_repay.label


def test_equal_figures_are_not_a_disagreement():
    """A company with no capital spending reports the two as equal. That is consistent."""
    out = debt_and_cash(_f(total_debt=20e9, total_cash=0.0,
                           operating_cash_flow=10e9, free_cash_flow=10e9))
    assert out.years_to_repay.available


def test_the_guard_holds_when_operating_cash_flow_is_unknown():
    """Nothing to compare against, so the guard cannot fire and the reading stands."""
    out = debt_and_cash(_f(total_debt=20e9, total_cash=0.0, free_cash_flow=10e9))
    assert out.years_to_repay.available


def test_the_adapter_prefers_the_statement_figure_over_the_headline():
    """item 2(a), at the source. NFLX's cash-flow statement says 9,461,053,000 - which is
    operating cash flow 10,149,273,000 minus capital spending 688,220,000, exactly. The
    info blob's 25,387,552,768 is a TTM figure on another basis."""
    from aristos_council.data.yfinance_adapter import _first_present

    assert _first_present([9_461_053_000, 6_921_826_000], 25_387_552_768) == 9_461_053_000
    assert _first_present([None, 6_921_826_000], 25_387_552_768) == 6_921_826_000
    # ...and the headline is still the fallback when there is no statement at all
    assert _first_present([], 25_387_552_768) == 25_387_552_768
    assert _first_present(None, 25_387_552_768) == 25_387_552_768


# =========================================================================== #
# ABS-READINGS-3 item 2 - the same sentence, said once
# =========================================================================== #
from aristos_council.abs_readings import dedupe_lines


def test_ATT_prints_its_EPS_abstention_ONCE_not_once_per_window():
    """T, 2026-09-20. Earnings per share was negative three years ago, so NEITHER window
    has a compound rate, and both windows produced the identical sentence - the page said
    it twice in a row."""
    out = growth_record(_f(aligned={"diluted_eps": [2.1, 1.8, 1.4, -0.7]}))
    lines = out.eps.lines()
    assert sum(1 for l in lines if "not positive" in l) == 1, lines
    assert len(lines) == len(set(lines))


def test_NVIDIA_says_it_has_no_net_debt_to_repay_ONCE_not_twice():
    """NVDA, 2026-09-20. Net cash, so both cash-flow lines - the operating one and the
    free one - resolved to the same sentence."""
    out = debt_and_cash(_f(total_debt=10e9, total_cash=34e9,
                           operating_cash_flow=60e9, free_cash_flow=55e9))
    lines = out.lines()
    assert sum(1 for l in lines if "no net debt to repay" in l) == 1, lines
    assert len(lines) == len(set(lines))


def test_the_READINGS_underneath_are_untouched_by_the_collapse():
    """A rendering decision, exactly like the span collapse. Both readings still exist,
    both still abstain, and each keeps its own note - only the printing is deduplicated."""
    out = debt_and_cash(_f(total_debt=10e9, total_cash=34e9,
                           operating_cash_flow=60e9, free_cash_flow=55e9))
    # Net cash is not an abstention: both readings are available, each with its own
    # zero, and they collapse only because the SENTENCE is the same one twice.
    assert out.net_debt_to_ocf.available and out.years_to_repay.available
    assert out.net_debt_to_ocf.value == 0.0 and out.years_to_repay.value == 0.0
    assert out.net_debt_to_ocf.text() == out.years_to_repay.text()   # why they collapsed

    record = growth_record(_f(aligned={"diluted_eps": [2.1, 1.8, 1.4, -0.7]}))
    assert set(record.eps.cagr) == set(GROWTH_WINDOWS)
    assert all(not record.eps.cagr[w].available for w in GROWTH_WINDOWS)


def test_two_DIFFERENT_sentences_both_survive():
    """The collapse must not swallow a second, genuinely different statement."""
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9,
                           operating_cash_flow=10e9, free_cash_flow=8e9))
    lines = out.lines()
    assert any("operating cash flow" in l for l in lines)
    assert any("free cash flow" in l for l in lines)


def test_the_collapse_keeps_the_FIRST_occurrence_and_the_original_order():
    assert dedupe_lines(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_spacing_and_capitalisation_do_not_smuggle_a_duplicate_through():
    """The two identical sentences were assembled by different code paths; a stray double
    space would have defeated a naive equality check."""
    assert dedupe_lines(["has no net debt to repay",
                         "has  no net debt  to repay",
                         "Has no net debt to repay"]) == ["has no net debt to repay"]


# =========================================================================== #
# CASH-RUNWAY-1 (approved 2026-10-04; Viking VKTX had negative free cash flow and net cash, no line)
# =========================================================================== #
def _viking(**kw):
    base = dict(total_debt=5.0e6, total_cash=502.6e6, free_cash_flow=-278.7e6,
                financial_currency="USD", trailing_period_end="2026-06-30")
    base.update(kw)
    return _f(**base)


def test_runway_negative_fcf_and_net_cash_says_how_long_the_cash_lasts():
    out = debt_and_cash(_viking())
    assert out.net_debt.label == "holds $497.6m more cash than debt"
    assert out.cash_runway.available
    assert out.cash_runway.label == ("at last year's spending ($278.7m) that lasts about 1.8 years "
                                     "(balance sheet of Jun 2026)")
    assert out.cash_runway.value == pytest.approx(497.6 / 278.7)
    assert out.cash_runway.label in out.lines()


def test_runway_without_a_period_end_says_latest_balance_sheet():
    out = debt_and_cash(_viking(trailing_period_end=None))
    assert out.cash_runway.label.endswith("(latest balance sheet)")


def test_runway_negative_fcf_and_net_debt_says_there_is_no_cushion():
    out = debt_and_cash(_viking(total_debt=900e6, total_cash=100e6))
    assert out.cash_runway.label == "no cash cushion: spending is funded by debt"
    assert "no cash cushion: spending is funded by debt" in out.lines()


def test_runway_positive_or_missing_fcf_gives_no_line():
    for fcf in (278.7e6, 0.0, None):
        out = debt_and_cash(_viking(free_cash_flow=fcf))
        assert not out.cash_runway.available
        assert not any("lasts about" in ln or "cushion" in ln for ln in out.lines())


def test_runway_missing_cash_or_debt_gives_no_line():
    assert not debt_and_cash(_viking(total_cash=None)).cash_runway.available
    assert not debt_and_cash(_viking(total_debt=None)).cash_runway.available


def test_runway_a_bank_gets_no_line():
    out = debt_and_cash(_viking(sector="Financial Services"))
    assert out.net_debt.not_meaningful and not out.cash_runway.available


def test_runway_reaches_the_story_the_text_page_and_the_summary_facts(tmp_path):
    from aristos_council.abs_readings import DebtAndCash  # noqa: F401
    from aristos_council.company_check import absolute_reading_lines
    from aristos_council.company_report import company_facts_pack, format_company_report
    from aristos_council.company_story import story_paragraphs
    from tests.test_company_report import RAW, _run
    report = _run([RAW], tmp_path=tmp_path)
    report.check.debt_and_cash = debt_and_cash(_viking())
    story = dict(story_paragraphs(report))
    survived = story["Other facts."]
    assert "that lasts about 1.8 years (balance sheet of Jun 2026)" in survived
    assert any("lasts about 1.8 years" in ln for ln in absolute_reading_lines(report.check))
    assert "lasts about 1.8 years" in format_company_report(report)
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.export.report_html import company_report_html
    assert "lasts about 1.8 years" in company_report_markdown(report)
    assert "lasts about 1.8 years" in company_report_html(report)
    assert any("lasts about 1.8 years" in ln
               for ln in company_facts_pack(report)["absolute_readings"]["debt_and_cash"])


def test_runway_under_a_year_is_said_in_months_and_under_a_month_as_such():
    """BYD (1211.HK): CNY 2.8bn net cash against CNY 97.7bn of spending printed "about 0.0 years"."""
    five_months = debt_and_cash(_viking(total_cash=5.0e6 + 116.1e6))      # net cash 116.1m / 278.7m
    assert "that lasts about 5 months (balance sheet of Jun 2026)" in five_months.cash_runway.label
    one_month = debt_and_cash(_viking(total_cash=5.0e6 + 25.0e6))         # 25m / 278.7m -> 1.08 months
    assert "that lasts about 1 month (" in one_month.cash_runway.label
    days = debt_and_cash(_viking(total_cash=5.0e6 + 2.8e6, free_cash_flow=-97.7e9))
    assert "that lasts less than a month (" in days.cash_runway.label
    assert "0.0 years" not in days.cash_runway.label
