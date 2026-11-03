"""ABSOLUTE-GATE-1 (docs/DESIGN_absolute.md, section b): the exclusion gate.

Every rule's pass, fail and not-evaluated case, every sign case, the combination rule, the bank
fixture, Z'' against a hand computation, the F-score nine-check rule, the Microsoft worked example, the
shipped gate file, the page section, and the additive saved record. No test reaches a real adapter.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from aristos_council.data.adapter import Fundamentals
from aristos_council.gate import (EXCLUDED, NOT_COVERED, NOT_ENOUGH_DATA, QUALIFIES, GateDefinitionError,
                                  evaluate_gate, load_gate, parse_gate)
from aristos_council.gate import engine, render
from aristos_council.tools import gate_readings as gr

D = load_gate()                                  # the shipped definition


def _f(**kw) -> Fundamentals:
    base = dict(ticker="X", name="X Corp", sector="Technology", currency="USD", financial_currency="USD")
    base.update(kw)
    return Fundamentals(**base)


# --------------------------------------------------------------------------- #
# the Microsoft worked example (design sections e and l): 10-K lines for the year to June 2026, in $
# millions as the filing prints them. FIXTURE-ONLY values are marked; none is claimed as a filing figure.
# --------------------------------------------------------------------------- #
M = 1e6


def msft() -> Fundamentals:
    return _f(
        ticker="MSFT", name="Microsoft Corporation", market_cap=3.67e12,
        net_income=[133_749 * M, 101_832 * M, 88_136 * M],
        operating_cash_flow_annual=[182_935 * M, 136_162 * M, 118_548 * M],
        capital_expenditure_annual=[-115_948 * M, -64_551 * M, -44_477 * M],
        free_cash_flow_annual=[66_987 * M, 71_611 * M, 74_071 * M],
        depreciation_amortization_annual=[38_534 * M, 29_433 * M, 20_958 * M],
        ebit=[155_237 * M, 128_528 * M, 109_433 * M],
        operating_income=[155_237 * M, 128_528 * M, 109_433 * M],
        total_assets_annual=[758_376 * M, 619_003 * M],
        total_liabilities_annual=[315_989 * M, 275_524 * M],
        shareholders_equity=[442_387 * M, 343_479 * M],
        current_assets_annual=[207_710 * M, 191_131 * M],
        current_liabilities_annual=[168_825 * M, 141_218 * M],
        retained_earnings_annual=[328_265 * M, 237_731 * M],
        long_term_debt_annual=[31_067 * M, 40_152 * M],
        # fixture-only values (not on the page): revenue and gross profit for the margin and turnover
        # checks, share counts, and the dividend paid
        total_revenue=[331_800 * M, 281_700 * M],
        gross_profit_annual=[230_000 * M, 193_000 * M],
        shares_outstanding_annual=[7_430 * M, 7_433 * M],
        dividends_paid=25_000 * M,
        # debt as the tool counts it: 9,227 + 31,067 + the 16,532 long-term operating lease line
        total_debt=56_826 * M, total_cash=76_843 * M)


def test_the_microsoft_worked_example_passes_every_rule_with_no_warnings():
    g = evaluate_gate(msft(), D)
    assert g.state == QUALIFIES
    by = {r.key: r for r in g.rules}
    assert by["earnings_quality"].result == "pass" and by["earnings_quality"].value < 0     # negative accruals
    assert by["distress"].result == "pass" and by["distress"].value > D.z_grey_to          # safe zone
    assert by["accounting_health"].result == "pass"
    assert by["cash_runway"].result == "not_applicable"                                    # makes cash
    lev = {w.key: w for w in g.warnings}
    assert lev["leverage"].result == "pass" and lev["leverage"].reading.startswith("net cash")
    assert lev["dividend_cover"].result == "pass" and lev["dividend_cover"].value > 1.5
    assert g.reason.endswith("No warnings.")


def test_the_microsoft_accrual_ratio_is_the_10k_arithmetic():
    # (133,749 - 182,935) / mean(758,376, 619,003) = -0.0714
    r = gr.earnings_quality(msft())
    assert r.value == pytest.approx((133_749 - 182_935) / ((758_376 + 619_003) / 2), rel=1e-9)


def test_the_microsoft_net_cash_is_the_tools_debt_definition_not_the_10k_borrowings_alone():
    """Reconciliation (design section l): the tool's debt row, 56,826m, is borrowings 9,227 + 31,067 plus the
    long-term operating lease line 16,532; cash is 76,843m. Net cash on that definition is 20.0bn."""
    debt, cash, source = gr.debt_and_cash_as_counted(msft())
    assert debt == pytest.approx((9_227 + 31_067 + 16_532) * M)
    assert cash - debt == pytest.approx(20_017 * M)
    assert source == "latest balance sheet"


def test_microsofts_ebitda_is_ebit_plus_depreciation_and_amortisation():
    assert gr.ebitda(msft()).value == pytest.approx((155_237 + 38_534) * M)


# --------------------------------------------------------------------------- #
# Z'' against a hand computation, and its sign cases
# --------------------------------------------------------------------------- #
def _z_inputs(**over):
    base = dict(current_assets_annual=[50.0], current_liabilities_annual=[30.0],
                retained_earnings_annual=[20.0], ebit=[10.0], total_assets_annual=[100.0],
                total_liabilities_annual=[60.0], shareholders_equity=[40.0])
    base.update(over)
    return _f(**base)


def test_z_double_prime_matches_a_hand_computation():
    # X1 = (50-30)/100 = .2   X2 = 20/100 = .2   X3 = 10/100 = .1   X4 = 40/60
    expected = 6.56 * 0.2 + 3.26 * 0.2 + 6.72 * 0.1 + 1.05 * (40 / 60)
    assert expected == pytest.approx(3.336, abs=1e-9)
    assert gr.altman_z_double_prime(_z_inputs()).value == pytest.approx(expected, rel=1e-12)


def test_z_double_prime_has_no_constant():
    """The constant of 3.25 is not added: the 1.10 distress line is for the form without it."""
    assert gr.altman_z_double_prime(_z_inputs()).value == pytest.approx(3.336)


def test_z_double_prime_total_assets_at_or_below_zero_is_not_evaluated():
    for ta in (0.0, -5.0):
        r = gr.altman_z_double_prime(_z_inputs(total_assets_annual=[ta]))
        assert r.value is None and "at or below zero" in r.note


def test_z_double_prime_total_liabilities_at_or_below_zero_is_not_evaluated():
    for tl in (0.0, -5.0):
        assert gr.altman_z_double_prime(_z_inputs(total_liabilities_annual=[tl])).value is None


def test_z_double_prime_negative_equity_is_computed_and_drags_the_score_down():
    r = gr.altman_z_double_prime(_z_inputs(shareholders_equity=[-30.0]))
    assert r.value == pytest.approx(6.56 * 0.2 + 3.26 * 0.2 + 6.72 * 0.1 + 1.05 * (-30 / 60))


def test_z_double_prime_missing_line_is_not_evaluated_and_names_it():
    r = gr.altman_z_double_prime(_z_inputs(shareholders_equity=[]))
    assert r.value is None and "book equity" in r.note


def test_z_double_prime_evaluates_where_the_old_z_abstains_on_a_currency_mismatch():
    """An ADR: price in USD, accounts in EUR. The 1968 Z-Score abstains (its fourth term mixes the two);
    Z'' reads only statement lines, so it evaluates."""
    from aristos_council.tools.screening import altman_z_score
    f = _z_inputs(currency="USD", financial_currency="EUR", market_cap=1e9, total_revenue=[200.0])
    assert altman_z_score(f).value is None
    assert gr.altman_z_double_prime(f).value is not None


def test_z_double_prime_abstains_when_the_provider_says_the_statements_are_not_in_one_currency():
    f = _z_inputs(statements_currency_note="revenue in CNY, statements in USD")
    assert gr.altman_z_double_prime(f).value is None


# --------------------------------------------------------------------------- #
# earnings quality: the accrual ratio, cash-flow form
# --------------------------------------------------------------------------- #
def _acc(ni, ocf, ta=(100.0, 100.0)):
    return _f(net_income=[ni], operating_cash_flow_annual=[ocf], total_assets_annual=list(ta))


def test_accrual_above_the_boundary_excludes_and_names_the_figure():
    r = engine._earnings_quality(_acc(30.0, 5.0), D)             # (30-5)/100 = 0.25
    assert r.result == "fail" and r.value == pytest.approx(0.25) and "0.25" in r.reading


def test_the_accrual_limit_is_printed_at_the_precision_of_the_frozen_boundary():
    """0.0054 must not read as 0.01 (two decimals) on the page."""
    r = engine._earnings_quality(_acc(30.0, 5.0), D)
    assert f"excluded above {D.accrual_exclude_above:g}" == r.limit and "0.0054" in r.limit


def test_negative_accruals_pass():
    assert engine._earnings_quality(_acc(5.0, 30.0), D).result == "pass"


def test_accrual_exactly_at_the_boundary_passes():
    ni = D.accrual_exclude_above * 100.0
    assert engine._earnings_quality(_acc(ni, 0.0), D).result == "pass"


def test_accrual_with_total_assets_at_or_below_zero_is_not_evaluated():
    for ta in ((0.0, 0.0), (-10.0, -10.0)):
        assert engine._earnings_quality(_acc(30.0, 5.0, ta), D).result == "not_evaluated"


def test_accrual_with_a_missing_input_is_not_evaluated():
    assert engine._earnings_quality(_f(net_income=[30.0], total_assets_annual=[100.0]), D).result == "not_evaluated"


def test_accrual_uses_the_mean_of_this_and_last_years_assets_and_discloses_a_single_year_fallback():
    r = gr.earnings_quality(_acc(30.0, 5.0, ta=(100.0, 300.0)))
    assert r.value == pytest.approx(25.0 / 200.0) and r.detail == ""
    one = gr.earnings_quality(_f(net_income=[30.0], operating_cash_flow_annual=[5.0],
                                 total_assets_annual=[100.0]))
    assert one.value == pytest.approx(0.25) and one.detail == "single-year assets"


# --------------------------------------------------------------------------- #
# accounting health: the F-score counts only with all nine checks
# --------------------------------------------------------------------------- #
def _nine(**over):
    base = dict(net_income=[10.0, 8.0], total_assets_annual=[100.0, 100.0],
                operating_cash_flow_annual=[15.0, 12.0], long_term_debt_annual=[10.0, 20.0],
                current_assets_annual=[60.0, 50.0], current_liabilities_annual=[30.0, 30.0],
                shares_outstanding_annual=[10.0, 10.0], gross_profit_annual=[40.0, 35.0],
                total_revenue=[100.0, 95.0])
    base.update(over)
    return _f(**base)


def test_the_f_score_counts_with_all_nine_checks():
    r = gr.accounting_health(_nine())
    assert r.computable == 9 and r.score == 9
    assert engine._accounting_health(_nine(), D).result == "pass"


def test_with_eight_checks_the_f_score_is_withheld_and_the_count_is_shown():
    f = _nine(gross_profit_annual=[])                            # two margin/turnover checks lose an input
    r = gr.accounting_health(f)
    assert r.computable < 9 and r.score is None
    rule = engine._accounting_health(f, D)
    assert rule.result == "not_evaluated" and f"{r.computable} of 9" in rule.reading


def test_a_score_of_zero_or_one_with_nine_checks_excludes():
    bad = _nine(net_income=[-5.0, 2.0], operating_cash_flow_annual=[-10.0, 4.0],
                long_term_debt_annual=[30.0, 20.0], current_assets_annual=[40.0, 50.0],
                shares_outstanding_annual=[12.0, 10.0], gross_profit_annual=[20.0, 35.0],
                total_revenue=[100.0, 95.0], total_assets_annual=[100.0, 80.0])
    r = gr.accounting_health(bad)
    assert r.score is not None and r.score <= 1
    assert engine._accounting_health(bad, D).result == "fail"


def test_a_score_of_two_passes_the_cut():
    spec = replace(D, f_exclude_at_or_below=1)
    # reuse the healthy fixture but degrade until the tally is exactly 2
    f = _nine(net_income=[-5.0, 2.0], operating_cash_flow_annual=[20.0, 4.0],
              long_term_debt_annual=[30.0, 20.0], current_assets_annual=[40.0, 50.0],
              shares_outstanding_annual=[12.0, 10.0], gross_profit_annual=[20.0, 35.0],
              total_revenue=[100.0, 95.0], total_assets_annual=[100.0, 80.0])
    r = gr.accounting_health(f)
    assert r.score == 2
    assert engine._accounting_health(f, spec).result == "pass"


# --------------------------------------------------------------------------- #
# cash runway
# --------------------------------------------------------------------------- #
def _burn(cash, fcf=-100.0, ocf=-80.0):
    return _f(operating_cash_flow_annual=[ocf], free_cash_flow_annual=[fcf], total_cash=cash)


def test_runway_does_not_apply_when_operating_cash_flow_is_positive():
    r = engine._cash_runway(_f(operating_cash_flow_annual=[50.0], free_cash_flow_annual=[-10.0],
                               total_cash=5.0), D)
    assert r.result == "not_applicable"


def test_runway_under_twelve_months_excludes():
    r = engine._cash_runway(_burn(cash=50.0), D)                 # 6 months
    assert r.result == "fail" and r.value == pytest.approx(6.0)


def test_runway_between_twelve_and_twenty_four_months_warns_and_does_not_exclude():
    r = engine._cash_runway(_burn(cash=150.0), D)                # 18 months
    assert r.result == "warn" and "raise money" in r.note


def test_runway_exactly_twelve_months_is_a_warning_not_an_exclusion():
    assert engine._cash_runway(_burn(cash=100.0), D).result == "warn"


def test_runway_of_twenty_four_months_or_more_passes():
    assert engine._cash_runway(_burn(cash=200.0), D).result == "pass"


def test_runway_with_cash_missing_is_not_evaluated():
    assert engine._cash_runway(_f(operating_cash_flow_annual=[-80.0], free_cash_flow_annual=[-100.0]), D).result \
        == "not_evaluated"


def test_runway_with_operating_cash_flow_missing_is_not_evaluated():
    assert engine._cash_runway(_f(total_cash=100.0), D).result == "not_evaluated"


def test_runway_burn_is_free_cash_flow_derived_from_capex_when_the_provider_gives_none():
    f = _f(operating_cash_flow_annual=[-20.0], capital_expenditure_annual=[-30.0], total_cash=100.0)
    assert gr.cash_runway(f).months == pytest.approx(12.0 * 100.0 / 50.0)


def test_runway_does_not_apply_when_free_cash_flow_is_not_negative():
    assert not gr.cash_runway(_f(operating_cash_flow_annual=[-5.0], free_cash_flow_annual=[3.0],
                                 total_cash=10.0)).applies


# --------------------------------------------------------------------------- #
# leverage warning
# --------------------------------------------------------------------------- #
def _lev(debt, cash, ebit, da):
    return _f(total_debt=debt, total_cash=cash, ebit=[ebit], depreciation_amortization_annual=[da])


def test_leverage_above_six_times_warns():
    r = engine._leverage(_lev(700.0, 0.0, 80.0, 20.0), D)            # 700 / 100 = 7.0
    assert r.result == "warn" and r.value == pytest.approx(7.0)


def test_leverage_of_exactly_six_times_does_not_warn():
    assert engine._leverage(_lev(600.0, 0.0, 80.0, 20.0), D).result == "pass"


def test_net_cash_is_a_pass_not_a_division():
    r = engine._leverage(_lev(100.0, 500.0, 80.0, 20.0), D)
    assert r.result == "pass" and "net cash" in r.reading


def test_ebitda_at_or_below_zero_is_not_evaluated_never_a_pass():
    for ebit, da in ((-50.0, 10.0), (-10.0, 10.0)):
        assert engine._leverage(_lev(700.0, 0.0, ebit, da), D).result == "not_evaluated"


def test_leverage_without_depreciation_is_not_evaluated():
    assert engine._leverage(_f(total_debt=700.0, total_cash=0.0, ebit=[80.0]), D).result == "not_evaluated"


def test_leverage_without_debt_or_cash_is_not_evaluated():
    assert engine._leverage(_f(ebit=[80.0], depreciation_amortization_annual=[20.0]), D).result == "not_evaluated"


def test_leverage_reads_the_newest_period_scalars_when_the_provider_gives_no_balance_sheet_scalars():
    f = _f(ebit=[80.0], depreciation_amortization_annual=[20.0],
           period_scalars={"2025-12-31": {"total_debt": 800.0, "total_cash": 100.0},
                           "2024-12-31": {"total_debt": 1.0, "total_cash": 1.0}})
    assert gr.leverage(f).value == pytest.approx(7.0)


# --------------------------------------------------------------------------- #
# dividend cover warning
# --------------------------------------------------------------------------- #
def _cover(ni, paid):
    return _f(net_income=list(ni), dividends_paid=paid)


def test_no_dividend_means_the_warning_does_not_apply():
    assert engine._dividend_cover(_cover([100.0], 0.0), D).result == "not_applicable"


def test_cover_below_one_reads_not_covered():
    r = engine._dividend_cover(_cover([90.0, 90.0, 90.0], 100.0), D)
    assert r.result == "warn" and "not covered" in r.reading


def test_cover_between_one_and_one_and_a_half_reads_thin():
    r = engine._dividend_cover(_cover([120.0, 120.0, 120.0], 100.0), D)
    assert r.result == "warn" and "thin" in r.reading


def test_cover_of_one_and_a_half_or_more_passes():
    assert engine._dividend_cover(_cover([150.0] * 3, 100.0), D).result == "pass"


def test_a_dividend_paid_on_average_net_income_at_or_below_zero_is_not_covered_never_a_pass():
    for ni in ([0.0, 0.0, 0.0], [-50.0, -20.0, 10.0]):
        r = engine._dividend_cover(_cover(ni, 100.0), D)
        assert r.result == "warn" and "not covered" in r.reading


def test_cover_averages_up_to_three_years_and_discloses_the_count():
    r = gr.dividend_cover(_cover([300.0, 0.0, 0.0, 9999.0], 100.0))
    assert r.cover == pytest.approx(1.0) and r.years == 3
    assert gr.dividend_cover(_cover([200.0], 100.0)).years == 1


def test_a_company_with_no_dividend_on_record_has_none_to_cover_even_when_the_cash_flow_line_is_empty():
    r = engine._dividend_cover(_f(net_income=[100.0]), D)
    assert r.result == "not_applicable" and r.reading == "no dividend paid"


def test_a_payer_whose_cash_flow_line_is_missing_stays_not_evaluated():
    r = engine._dividend_cover(_f(net_income=[100.0], dividend_per_share=1.2, dividend_yield=0.03), D)
    assert r.result == "not_evaluated"


def test_cover_reads_the_newest_periods_dividend_when_the_provider_gives_no_scalar():
    f = _f(net_income=[300.0, 300.0, 300.0],
           period_scalars={"2025-12-31": {"dividends_paid": 100.0}, "2024-12-31": {"dividends_paid": 1.0}})
    assert gr.dividend_cover(f).cover == pytest.approx(3.0)


def test_cover_with_net_income_missing_is_not_evaluated():
    assert engine._dividend_cover(_f(dividends_paid=100.0), D).result == "not_evaluated"


# --------------------------------------------------------------------------- #
# the combination rule
# --------------------------------------------------------------------------- #
def test_any_failed_exclusion_excludes_and_names_the_rule_and_the_figure():
    f = replace(msft(), net_income=[500_000 * M, 101_832 * M])      # huge accrual, everything else fine
    g = evaluate_gate(f, D)
    assert g.state == EXCLUDED
    assert "Earnings quality" in g.reason and "excluded above" in g.reason


def test_two_failures_are_both_named():
    f = replace(msft(), net_income=[500_000 * M, 1.0],
                shareholders_equity=[-900_000 * M, 1.0])            # distress as well
    g = evaluate_gate(f, D)
    assert g.state == EXCLUDED and "Earnings quality" in g.reason and "Distress" in g.reason


def test_warnings_never_change_the_state():
    f = replace(msft(), dividends_paid=900_000 * M, total_debt=5e12)
    g = evaluate_gate(f, D)
    assert g.state == QUALIFIES
    assert {w.key for w in g.warnings if w.result == "warn"} == {"leverage", "dividend_cover"}
    assert "Warnings:" in g.reason


def test_qualifies_needs_z_double_prime_and_the_accrual_ratio_both_evaluated():
    no_z = replace(msft(), shareholders_equity=[])
    g = evaluate_gate(no_z, D)
    assert g.state == NOT_ENOUGH_DATA and any("Z''" in m for m in g.missing)
    no_acc = replace(msft(), net_income=[])
    g2 = evaluate_gate(no_acc, D)
    assert g2.state == NOT_ENOUGH_DATA and any("accrual" in m for m in g2.missing)


def test_a_withheld_f_score_does_not_block_qualifying():
    g = evaluate_gate(replace(msft(), gross_profit_annual=[]), D)
    assert g.state == QUALIFIES
    assert {r.key: r for r in g.rules}["accounting_health"].result == "not_evaluated"


def test_a_runway_that_does_not_apply_does_not_block_qualifying():
    g = evaluate_gate(msft(), D)
    assert {r.key: r for r in g.rules}["cash_runway"].result == "not_applicable" and g.state == QUALIFIES


def test_no_accounts_is_not_enough_data():
    g = evaluate_gate(None, D)
    assert g.state == NOT_ENOUGH_DATA and g.missing == ("accounts",)


def test_a_bank_reads_not_covered_by_the_sector_the_other_absolute_readings_use():
    """Banks and insurers: Fundamentals.sector, via abs_readings.is_financial_sector."""
    for sector in ("Financial Services", "Financials"):
        g = evaluate_gate(replace(msft(), sector=sector), D)
        assert g.state == NOT_COVERED and not g.rules and "banks and insurers" in g.reason
    assert engine.NOT_COVERED_FIELD == "Fundamentals.sector"


def test_a_missing_sector_is_never_a_bank():
    assert evaluate_gate(replace(msft(), sector=None), D).state == QUALIFIES


# --------------------------------------------------------------------------- #
# abnormal investment (measured here, used in phase 2)
# --------------------------------------------------------------------------- #
def test_abnormal_investment_is_capex_over_sales_against_its_prior_three_year_average_minus_one():
    f = _f(capital_expenditure_annual=[-30.0, -10.0, -10.0, -10.0], total_revenue=[100.0] * 4)
    assert gr.abnormal_investment(f).value == pytest.approx(0.30 / 0.10 - 1.0)


def test_abnormal_investment_needs_four_years_and_positive_sales():
    assert gr.abnormal_investment(_f(capital_expenditure_annual=[-1.0] * 3, total_revenue=[10.0] * 3)).value is None
    assert gr.abnormal_investment(_f(capital_expenditure_annual=[-1.0] * 4, total_revenue=[10.0, 10.0, 0.0, 10.0])).value is None


# --------------------------------------------------------------------------- #
# the shipped gate file
# --------------------------------------------------------------------------- #
def test_the_gate_file_loads_with_every_threshold_the_design_names():
    assert D.id == "absolute_gate_v1" and D.version == 1
    assert D.z_exclude_below == 1.10 and D.z_grey_to == 2.60
    assert D.f_exclude_at_or_below == 1 and D.f_required_checks == 9
    assert (D.runway_exclude_below_months, D.runway_warn_below_months) == (12, 24)
    assert D.leverage_warn_above == 6.0
    assert (D.cover_not_covered_below, D.cover_thin_below, D.cover_years) == (1.0, 1.5, 3)


def test_every_rule_and_warning_carries_a_source_line():
    for block in (D.raw["rules"], D.raw["warnings"]):
        for key, rule in block.items():
            assert str(rule.get("source") or "").strip(), key


def test_a_missing_threshold_is_refused_at_load():
    doc = {"id": "x", "version": 1, "status": "frozen", "rules": {}, "warnings": {}}
    with pytest.raises(GateDefinitionError):
        parse_gate(doc)


def test_the_frozen_gate_file_records_its_measured_boundaries():
    """Step 5: the 0.10 stand-in is replaced by the measured 90th percentile, with the calibration date."""
    assert D.frozen and D.accrual_boundary_status == "measured"
    assert D.accrual_exclude_above != 0.10
    assert D.calibration.get("date") and D.calibration.get("universe_size")
    assert D.intensity_warn_above is not None


# --------------------------------------------------------------------------- #
# the page section and the record
# --------------------------------------------------------------------------- #
def test_the_section_has_four_exclusions_and_two_warnings_in_plain_english():
    g = evaluate_gate(msft(), D)
    rows = render.rows(g)
    assert [r[1] for r in rows] == ["exclusion"] * 4 + ["warning"] * 2
    text = "\n".join(render.text_lines(g))
    assert text.startswith(render.SECTION_TITLE.upper())
    assert "does not change the verdict yet" in text.lower() and "Gate result: Qualifies" in text
    assert "Total Debt row" in text and "long-term operating lease line" in text
    for banned in ("accrual_ratio", "altman_z", "piotroski_f_score", "min_f_score", "strategy_id"):
        assert banned not in text


def test_a_bank_section_has_no_table_and_says_not_covered():
    g = evaluate_gate(replace(msft(), sector="Financial Services"), D)
    text = "\n".join(render.text_lines(g))
    assert "Gate result: Not covered" in text and "Check  Kind" not in text


def test_the_gate_record_is_json_ready():
    import json
    d = evaluate_gate(msft(), D).as_dict()
    assert json.loads(json.dumps(d))["state"] == QUALIFIES
    assert [r["key"] for r in d["rules"]] == ["earnings_quality", "distress", "accounting_health", "cash_runway"]


@pytest.mark.min_group(3)
def test_a_company_report_carries_the_gate_in_every_download_and_the_record_is_additive(tmp_path):
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import format_company_report, report_record
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run

    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.check.gate is not None
    text, md, html = format_company_report(report), company_report_markdown(report), company_report_html(report)
    assert render.SECTION_TITLE.upper() in text and "Gate result:" in text
    assert f"## {render.SECTION_TITLE}" in md and "| Check | Kind | Reading | Limit | Result |" in md
    assert 'id="gate"' in html and render.SECTION_TITLE in html
    with_gate = report_record(report)
    assert with_gate["gate"]["state"] == report.check.gate.state
    report.check.gate = None
    without = report_record(report)
    assert "gate" not in without
    assert {k: v for k, v in with_gate.items() if k != "gate"} == without     # nothing else moved


@pytest.mark.min_group(3)
def test_the_record_keys_are_the_v2_keys_plus_gate(tmp_path):
    from aristos_council.company_report import report_record
    from tests.test_company_report import RAW, _run
    keys = set(report_record(_run([RAW], tmp_path=tmp_path, save=False)))
    assert keys == {"schema_version", "kind", "ticker", "company", "run_at", "lenses", "peer_snapshot",
                    "peer_sentence", "universe", "votes", "agreement", "no_vote_reason", "cohort_slug",
                    "track_record_caption", "track_record_summary", "lens_ranks", "sources", "seconds",
                    "cache", "summary_written", "council_opinion", "outside_tested_range",
                    "smallcap_cohort", "smallcap_floor_usd", "smallcap_band_note", "size_matched_peers",
                    "price_and_cash", "gate"}
