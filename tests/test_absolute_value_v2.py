"""ABSOLUTE-PAGE-1, last commit: valuation definition version 2 (docs/DESIGN_absolute.md decision 17).

One change from version 1: years 1 and 2 of the downside case use the LOWER of the consensus rate and the own 5-year
rate (whichever exists if one is missing). Reason: when analysts forecast 50% a year, two years at that rate is not a
downside. Version 1 stays on disk, loadable, as the record.

Anchored to Microsoft as the live page reads it (consensus 18.3% from the analysts' own year-ago figure, own 5-year
growth 17.2%): the downside case then starts at 17.2% and returns 4.6%, the same as the backtest form.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aristos_council.absolute_value.definition import (CONSENSUS_ELSE_OWN_5, LOWER_OF_CONSENSUS_AND_OWN_5,
                                                       load_definition)
from aristos_council.absolute_value.engine import DOWNSIDE
from aristos_council.data.analyst_trend import TrendData, TrendPeriod
from tests.test_absolute_value import record, run

ROOT = Path(__file__).resolve().parents[1]
V1, V2 = load_definition("absolute_value_v1"), load_definition("absolute_value_v2")


def live_trend(year_ago=17.28, now=19.85, nxt=24.18):
    """Microsoft as the live page reads it: the analysts' own last-reported EPS is the consensus case's start."""
    return TrendData(current=TrendPeriod(period_end="2027-06-30", now=now, analysts=30, year_ago_eps=year_ago),
                     next_year=TrendPeriod(period_end="2028-06-30", now=nxt, analysts=28))


def downside(av):
    return next(c for c in av.cases if c.key == DOWNSIDE)


def test_version_2_is_the_default_and_version_1_stays_on_disk_unchanged_in_every_number():
    assert load_definition().id == "absolute_value_v3" and V2.id == "absolute_value_v2"
    assert load_definition().downside_fast_rule == V2.downside_fast_rule == LOWER_OF_CONSENSUS_AND_OWN_5
    assert V1.id == "absolute_value_v1" and V1.downside_fast_rule == CONSENSUS_ELSE_OWN_5
    a, b = yaml.safe_load((ROOT / "valuation/absolute_value_v1.yaml").read_text(encoding="utf-8")), \
        yaml.safe_load((ROOT / "valuation/absolute_value_v2.yaml").read_text(encoding="utf-8"))
    for key in ("base", "hurdle", "solver", "high_growth_mark", "verdict_states", "investment_intensity"):
        assert a[key] == b[key], key                                  # the ONE change is the downside's first years
    assert a["cases"]["own_5_year"] == b["cases"]["own_5_year"] and a["cases"]["consensus"] == b["cases"]["consensus"]
    assert a["cases"]["downside"]["fast_years"] == b["cases"]["downside"]["fast_years"] == 2
    assert b["cases"]["downside"]["fast_rule"] == "lower_of_consensus_and_own_5_year"
    assert "fast_rule" not in a["cases"]["downside"]


def test_the_two_files_say_which_supersedes_which_and_why():
    v1 = (ROOT / "valuation/absolute_value_v1.yaml").read_text(encoding="utf-8")
    v2 = (ROOT / "valuation/absolute_value_v2.yaml").read_text(encoding="utf-8")
    assert v1.startswith("# SUPERSEDED by valuation/absolute_value_v2.yaml")
    assert "SUPERSEDES absolute_value_v1.yaml" in v2 and "supersedes: absolute_value_v1" in v2
    assert "two years at that rate is not a downside" in v2
    design = (ROOT / "docs/DESIGN_absolute.md").read_text(encoding="utf-8")
    assert "two years at that rate is not a downside" in design and "absolute_value_v2.yaml" in design


def test_microsoft_downside_starts_at_the_own_5_year_rate_and_returns_the_same_as_the_backtest_form():
    av = run(tr=live_trend())                                          # consensus 18.3% > own 5-year 17.2%
    cons = next(c for c in av.cases if c.key == "consensus")
    assert round(100 * cons.growth, 1) == 18.3
    d = downside(av)
    assert d.growth == pytest.approx(0.172)                            # starts at 17.2%, not 18.3%
    assert d.growth_text.startswith("17.2% for 2 years (own 5-year)")
    assert round(100 * d.ret, 1) == 4.9
    backtest_down = next(c for c in av.backtest_cases if c.key == DOWNSIDE)
    assert d.ret == pytest.approx(backtest_down.ret)                   # the same as the backtest form
    assert av.verdict.state == "qualifies_below_hurdle" and av.verdict.of == 4


def test_version_1_would_have_started_the_same_company_at_the_consensus_rate():
    av = run(tr=live_trend(), definition=V1)
    d = downside(av)
    assert d.growth == pytest.approx(0.1830, abs=1e-3) and d.growth_text.startswith("18.3% for 2 years (consensus)")
    assert round(100 * d.ret, 1) == 4.8          # a faster start earns more, so version 1's "downside" was the kinder one
    assert d.ret > next(c for c in av.backtest_cases if c.key == DOWNSIDE).ret


def test_when_the_consensus_is_the_lower_rate_it_is_still_the_start_so_the_design_page_numbers_hold():
    av = run()                                                         # consensus 16.0% < own 5-year 17.2%
    d = downside(av)
    assert d.growth == pytest.approx(0.1597, abs=1e-3) and d.growth_text.startswith("16.0% for 2 years (consensus)")
    assert round(100 * d.ret, 1) == 4.6


def test_a_company_the_analysts_forecast_at_fifty_percent_has_a_downside_that_starts_at_its_own_record():
    """ASML and TSMC on 2026-11-03: consensus about 50%, own 5-year 24% and 27%. Version 1 read 'clears' on the downside."""
    big = live_trend(year_ago=10.0, now=15.0, nxt=22.5)                # (22.5 / 10) ** 0.5 - 1 = 50%
    v2 = run(tr=big, rec=record(own5=0.239, own10=0.227))
    v1 = run(tr=big, rec=record(own5=0.239, own10=0.227), definition=V1)
    d2, d1 = downside(v2), downside(v1)
    assert d2.growth == pytest.approx(0.239) and d2.growth_text.startswith("23.9% for 2 years (own 5-year)")
    assert d1.growth == pytest.approx(0.50, abs=1e-3)
    assert d2.ret < d1.ret                                             # a real downside, not above the company's record
    own5 = next(c for c in v2.cases if c.key == "own_5_year")
    assert d2.ret < own5.ret


def test_a_missing_rate_falls_back_to_whichever_exists():
    no_consensus = run(tr=TrendData(), rec=record())                   # no analyst data at all
    assert downside(no_consensus).growth == pytest.approx(0.172)
    no_own = run(tr=live_trend(), rec=record(own5=None))               # earnings per share was not positive 5 years ago
    assert downside(no_own).growth == pytest.approx(0.1830, abs=1e-3)
    neither = run(tr=TrendData(), rec=record(own5=None))
    d = downside(neither)
    assert not d.available and d.reason == "no own 5-year rate to start from"


def test_the_backtest_form_is_unchanged_by_the_revision():
    for definition in (V1, V2):                    # both on the older base (operating cash flow as reported)
        av = run(tr=live_trend(), definition=definition)
        back = next(c for c in av.backtest_cases if c.key == DOWNSIDE)
        assert back.growth == pytest.approx(0.172) and round(100 * back.ret, 1) == 4.6


def test_an_unknown_rule_in_a_definition_file_is_refused_not_guessed():
    from aristos_council.absolute_value.definition import ValueDefinitionError, parse_definition
    doc = yaml.safe_load((ROOT / "valuation/absolute_value_v2.yaml").read_text(encoding="utf-8"))
    doc["cases"]["downside"]["fast_rule"] = "the_average_of_both"
    with pytest.raises(ValueDefinitionError):
        parse_definition(doc)
