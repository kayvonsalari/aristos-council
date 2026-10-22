"""GROWTH-CAP-WORDING-1 - the Growth BUY cap says whose median it is, and is listed as a rule.

The cap (growth_garp_v3 ``buy_requires: {revenue_growth: top_half}``) compares a name's sales-growth
rank with the OTHER NAMES THIS RUN RANKED. It read "growing slower than the industry median" on a
list run, where the median is of the pasted list or cohort. A list run now says "the median of this
list", the company page "the median of its peer group". The cap is also stated with the lens's
rules: "a BUY needs sales growth in the top half of the ranked names" - in the list report's "Rules
applied" section, and on the company page (which has no rules section) beside what the lens asks.

The strategy YAML's ``asks`` caption still says "above the industry median"; it is immutable here.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aristos_council.company_markdown import company_report_markdown
from aristos_council.company_report import LensVote, votes_from_multi
from aristos_council.company_story import table_rows
from aristos_council.pipeline import MultiStrategyCell, _ranker_filter_lines
from aristos_council.rank_engine import (FactorSpec, buy_requirement_lines, cap_reason_for_peers,
                                         rank_universe)
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.export.report_html import company_report_html
from tests.test_company_report import RAW, SCREENED, _run
from tests.test_company_story import _report


def _html_text(page: str) -> str:
    import html as _h
    import re as _re
    return _h.unescape(_re.sub(r"<[^>]+>", "", page))

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
GROWTH = load_rank_strategy(STRAT_DIR / "growth_garp_v3.yaml")
SPECS = [FactorSpec("revenue_growth"), FactorSpec("roic"), FactorSpec("earnings_yield")]
RULE = "a BUY needs sales growth in the top half of the ranked names"


def _capped():
    rows = [("SLOW", {"revenue_growth": 0.0, "roic": 1.0, "earnings_yield": 1.0})]
    rows += [(f"T{i}", {"revenue_growth": 0.1 + i * 0.01, "roic": 0.1, "earnings_yield": 0.1})
             for i in range(9)]
    out = rank_universe(rows, SPECS, buy_requires={"revenue_growth": "top_half"})
    return next(r for r in out if r.ticker == "SLOW")


def test_list_run_cap_reason_names_the_median_of_this_list():
    slow = _capped()
    assert slow.verdict == "hold"
    assert slow.cap_reason == "ranked 1st, but growing slower than the median of this list, so HOLD"
    assert "industry" not in slow.cap_reason


def test_other_factors_and_top_third_say_this_list_too():
    rows = [(f"T{i}", {"revenue_growth": 0.1 * i, "roic": 1.0 - 0.1 * i, "earnings_yield": 0.5})
            for i in range(10)]
    out = rank_universe(rows, SPECS, buy_requires={"roic": "top_third"})
    capped = [r.cap_reason for r in out if r.cap_reason]
    for reason in capped:
        assert "top third of this list" in reason and "industry" not in reason


def test_company_page_cap_reason_names_its_peer_group():
    reason = _capped().cap_reason
    assert cap_reason_for_peers(reason) == \
        "ranked 1st, but growing slower than the median of its peer group, so HOLD"
    assert cap_reason_for_peers("") == ""


def test_votes_from_multi_hands_the_company_page_the_peer_group_wording():
    sid = "growth_garp_v3"
    cell = MultiStrategyCell(strategy_id=sid, status="ranked", position=1, cohort_size=10,
                             verdict="hold", cap_reason=_capped().cap_reason)
    multi = SimpleNamespace(
        rows=[SimpleNamespace(ticker="ACM", cells={sid: cell})], strategy_ids=[sid],
        results={sid: SimpleNamespace(rank_strategy=GROWTH, ranked=[])},
        strategy_names={sid: "Growth"})
    vote = votes_from_multi(multi, "ACM")[0]
    assert vote.cap_reason.endswith("the median of its peer group, so HOLD")
    assert vote.buy_rule == RULE


def test_the_rule_in_words():
    assert buy_requirement_lines({"revenue_growth": "top_half"}) == [RULE]
    assert buy_requirement_lines(None) == []
    assert buy_requirement_lines({"roic": "top_third"})[0].endswith(
        "in the top third of the ranked names")


def test_list_rules_applied_states_the_growth_buy_cap():
    lines = _ranker_filter_lines(GROWTH)
    assert f"BUY cap: {RULE} (otherwise HOLD)." in lines
    # it sits with the ranking rules, right after the factors it ranks on
    assert lines.index(f"BUY cap: {RULE} (otherwise HOLD).") == \
        next(i for i, line in enumerate(lines) if line.startswith("Names ranked on:")) + 1


def test_a_lens_without_a_cap_lists_no_cap():
    momentum = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")
    assert not any(line.startswith("BUY cap") for line in _ranker_filter_lines(momentum))


def test_company_page_states_the_cap_beside_what_growth_asks(tmp_path):
    growth = LensVote("growth_garp_v3", "Growth", status="ranked", verdict="hold", position=1,
                      cohort_size=10, asks="Growth asks a plain question.", buy_rule=RULE,
                      cap_reason=cap_reason_for_peers(_capped().cap_reason))
    row = table_rows(_report([growth]))[0]
    assert row.reason == "ranked 1st, but growing slower than the median of its peer group, so HOLD"
    assert row.asks_note == f"Growth asks a plain question. BUY cap: {RULE} (otherwise HOLD)."
    rep = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    rep.votes = [*rep.votes, growth]
    expected = f"Growth asks: Growth asks a plain question. BUY cap: {RULE} (otherwise HOLD)."
    assert expected in company_report_markdown(rep)
    assert expected in _html_text(company_report_html(rep))


def test_a_lens_without_a_cap_keeps_its_footnote_unchanged():
    quality = LensVote("quality_v1", "Quality", status="ranked", verdict="hold", position=2,
                       cohort_size=10, asks="Quality asks a plain question.")
    row = table_rows(_report([quality]))[0]
    assert row.asks_note == "Quality asks a plain question."


def test_rules_applied_block_carries_the_cap_for_a_growth_result():
    from aristos_council.pipeline import format_rules_applied, rules_applied
    result = SimpleNamespace(rank_strategy=GROWTH, screen_strategy=None, screen_outcomes={},
                             screen_bases={}, meta={})
    assert f"BUY cap: {RULE} (otherwise HOLD)." in rules_applied(result).ranker_lines
    assert any(RULE in line for line in format_rules_applied(result))
