"""ABSOLUTE-PAGE-2: the own-record rule for "clears", and the pre-working-capital base (definition version 3).

Ford is the fixture for the first rule: on 2026-11-04 it read "clears hurdle" on two analyst-driven cases (consensus and a
downside that starts at the consensus rate) because earnings per share was not positive five and ten years ago, so it had no
record of its own growth. Microsoft re-anchors the base: cash from operations before working-capital changes 187,830m,
capital spending 115,948m, shares 7,453m. No test reaches an adapter, a bond source or a model.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council import company_page as cp
from aristos_council.absolute_value import render as vrender
from aristos_council.absolute_value.definition import load_definition
from aristos_council.absolute_value.engine import (BELOW_HURDLE, CLEARS_HURDLE, NO_OWN_RECORD, NOT_ENOUGH_DATA,
                                                   build_absolute, free_cash_flow_base)
from tests.test_absolute_page import absolute, bond_source, fake_report, gate_ok, msft, record, trend
from tests.test_absolute_value import M, TODAY, run

ROOT = Path(__file__).resolve().parents[1]


def ford(price=12.31):
    """No own record (both growth legs unavailable), analysts forecasting a recovery: only the forecast cases exist."""
    return build_absolute(msft(), gate=gate_ok(), last_close=price, growth_record=record(own5=None, own10=None),
                          trend=trend(), today=TODAY, bond_source=bond_source)


# --------------------------------------------------------------------------- #
# rule 1: "clears" needs the company's own record
# --------------------------------------------------------------------------- #
def test_forecast_cases_alone_never_clear_the_hurdle_the_verdict_is_not_enough_data():
    av = ford(price=40.0)                                 # cheap enough that both forecast-based cases clear
    available = {c.key for c in av.cases if c.available}
    assert available == {"consensus", "downside"}
    assert all(c.clears(av.hurdle.hurdle) for c in av.cases if c.available)
    assert av.verdict.state == NOT_ENOUGH_DATA and av.verdict.reason == NO_OWN_RECORD == \
        "no record of the company's own growth"
    assert av.backtest_verdict.state == NOT_ENOUGH_DATA          # the backtest form has no own record either


def test_the_answer_line_and_the_story_say_so_and_still_show_the_forecast_cases_as_information():
    rep = fake_report(gate_ok(), ford(price=40.0))
    first, second = cp.answer_lines(rep)
    assert first == "Not enough data: no record of the company's own growth."
    assert "shown as information" in second
    paras = dict(cp.story_paragraphs(rep))
    assert "The growth cases return analyst consensus" in paras["What the price requires."]
    assert "no verdict is given; the cases above are information" in paras["What the price requires."]
    text = "\n".join(vrender.text_lines(rep.check.absolute))
    assert "THE PRICE TEST" in text and "THE RETURN RANGE" in text and "Analyst consensus" in text
    assert "Verdict: Not enough data: no record of the company's own growth." in text


def test_an_own_record_case_that_clears_with_every_case_clearing_still_reads_clears():
    av = absolute(gate_ok(), price=100.0)
    assert av.verdict.state == CLEARS_HURDLE and av.verdict.clearing == av.verdict.of == 4


def test_an_own_record_that_falls_short_is_below_hurdle_even_when_the_forecasts_clear():
    av = run(price=300.0)
    assert av.verdict.state == BELOW_HURDLE
    assert any(c.key == "consensus" and c.available for c in av.cases)


def test_one_own_record_case_is_enough_to_have_a_record():
    av = build_absolute(msft(), gate=gate_ok(), last_close=100.0, growth_record=record(own5=0.172, own10=None),
                        trend=trend(), today=TODAY, bond_source=bond_source)
    assert av.verdict.state == CLEARS_HURDLE and {c.key for c in av.cases if c.available} >= {"own_5_year"}


# --------------------------------------------------------------------------- #
# rule 2: the base is cash from operations before working-capital changes (version 3)
# --------------------------------------------------------------------------- #
def test_the_microsoft_base_is_187830_minus_115948_over_7453_shares():
    av = run()
    b = av.base
    assert round(b.base, 4) == round((187_830 - 115_948) / 7_453, 4) == pytest.approx(9.6447, abs=1e-4)
    assert b.pre_wc and av.definition_id == "absolute_value_v3"
    assert round(100 * av.implied_growth, 1) == 24.0
    assert [round(100 * c.ret, 1) for c in av.cases] == [6.8, 8.6, 6.3, 4.6]
    assert av.verdict.state == BELOW_HURDLE and av.verdict.count_text() == "0 of 4 cases clear"


def test_the_working_capital_effect_of_the_latest_year_is_its_own_line_in_the_price_test():
    text = "\n".join(vrender.text_lines(run()))
    assert "Working-capital effect of the latest year: it took cash of $0.66 a share, which the base leaves out" in text
    assert "Cash from operations is taken before working-capital changes, minus capital spending." in text
    released = msft(aligned_annual={**msft().aligned_annual, "change_in_working_capital": [5_000 * M, -1_000 * M, -2_000 * M]})
    assert "it released cash of $0.67 a share" in "\n".join(vrender.text_lines(run(released)))


def test_version_2_read_operating_cash_flow_as_reported_and_still_does():
    v2 = load_definition("absolute_value_v2")
    b = free_cash_flow_base(msft(), v2)
    assert not b.pre_wc and round(b.base, 2) == 8.99 and b.wc_effect is None


def test_a_missing_working_capital_line_is_not_enough_data_never_a_guess():
    f = msft(aligned_annual={**msft().aligned_annual, "change_in_working_capital": [None, -1_000 * M, -2_000 * M]})
    b = free_cash_flow_base(f, load_definition())
    assert b.base is None and "change in working capital missing" in b.note
    av = run(f)
    assert av.verdict.state == NOT_ENOUGH_DATA and "change in working capital" in av.verdict.reason


def test_a_yield_above_25_percent_is_marked_and_a_normal_one_is_not():
    cheap = run(price=30.0)                               # 9.64 / 30 = 32%
    assert cheap.yield_high
    text = "\n".join(vrender.text_lines(cheap))
    assert "Free cash flow yield on the base: 32.1%. Unusually high; check the cash flow lines." in text
    assert "which is unusually high; check the cash flow lines" in dict(
        cp.story_paragraphs(fake_report(gate_ok(), cheap)))["What to doubt."]
    normal = run()
    assert not normal.yield_high and "Unusually high" not in "\n".join(vrender.text_lines(normal))


def test_the_version_3_file_records_why_and_supersedes_version_2():
    v3 = (ROOT / "valuation/absolute_value_v3.yaml").read_text(encoding="utf-8")
    v2 = (ROOT / "valuation/absolute_value_v2.yaml").read_text(encoding="utf-8")
    assert v2.startswith("# SUPERSEDED by valuation/absolute_value_v3.yaml")
    assert "supersedes: absolute_value_v2" in v3 and "before_working_capital_changes" in v3
    assert "Korea Gas" in v3 and "107%" in v3 and "one definition of cash from operations" in v3.lower()
    design = (ROOT / "docs/DESIGN_absolute.md").read_text(encoding="utf-8")
    assert "absolute_value_v3.yaml" in design and "187,830" in design
