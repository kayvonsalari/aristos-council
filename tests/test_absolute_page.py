"""ABSOLUTE-PAGE-1 (docs/DESIGN_absolute.md sections f, h, i): the company page built around the absolute verdict.

What is pinned: the two-line answer and the five-paragraph story for every verdict state (Microsoft below the
hurdle, as on the design page; a fake excluded on accruals; a bank that is not covered; a loss-maker with not enough
data; a fake that clears); the page order in the text, the Markdown and the HTML downloads (the Streamlit page reads
the same blocks); the record's new keys and the old keys unchanged; and that a saved old file is never touched.
No test reaches a real adapter, a bond source or a model.
"""
from __future__ import annotations

import json
import re
from datetime import date
from types import SimpleNamespace

import pytest

from aristos_council import company_page as cp
from aristos_council.abs_readings import Reading
from aristos_council.absolute_value import render as vrender
from aristos_council.absolute_value.engine import (BELOW_HURDLE, CLEARS_HURDLE, EXCLUDED, NOT_COVERED,
                                                   NOT_ENOUGH_DATA, build_absolute)
from aristos_council.company_report import (LensVote, format_company_report, report_record,
                                            save_company_report)
from aristos_council.data import bond_yield as by
from aristos_council.data.adapter import Fundamentals
from aristos_council.data.analyst_trend import TrendData, TrendPeriod
from aristos_council.gate import render as grender
from aristos_council.gate.engine import (FAIL, NOT_APPLICABLE, NOT_EVALUATED, PASS, WARN, GateResult,
                                         RuleResult)
from aristos_council.company_markdown import company_report_markdown
from aristos_council.export.report_html import company_report_html
from aristos_council.report_sweep import visible_text

# the full-report fixtures are built under the old three-name peer rule (see tests/conftest.py)
pytestmark = pytest.mark.min_group(3)

TODAY = date(2026, 11, 3)
M = 1e6
BANNED = re.compile(r"expected return|target|proven|\bBUY\b|\bHOLD\b|\bSELL\b", re.I)
BANNED_CASE = re.compile(r"\b(?:BUY|HOLD|SELL)\b")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def msft(**over) -> Fundamentals:
    ends = ["2026-06-30", "2025-06-30", "2024-06-30"]
    kw = dict(
        ticker="MSFT", currency="USD", financial_currency="USD", sector="Technology",
        aligned_annual={
            "operating_cash_flow": [182_935 * M, 136_162 * M, 118_548 * M],
            "capital_expenditure": [-115_948 * M, -64_551 * M, -44_477 * M],
            "diluted_shares": [7_453 * M, 7_465 * M, 7_469 * M],
            "depreciation_amortization": [38_534 * M, 29_433 * M, 20_958 * M],
            "diluted_eps": [17.95, 13.64, 11.80],
            # ABSOLUTE-PAGE-2: the cash flow statement's change in working capital (positive = cash released), so cash
            # from operations before it is 187,830m for FY2026
            "change_in_working_capital": [-4_895 * M, -1_000 * M, -2_000 * M]},
        aligned_period_ends={"operating_cash_flow": ends, "capital_expenditure": ends,
                             "diluted_shares": ends, "diluted_eps": ends, "depreciation_amortization": ends,
                             "change_in_working_capital": ends},
        capital_expenditure_annual=[-115_948 * M, -64_551 * M, -44_477 * M, -28_107 * M],
        total_revenue=[331_800 * M, 281_700 * M, 245_100 * M, 211_900 * M],
        depreciation_amortization_annual=[38_534 * M, 29_433 * M, 20_958 * M],
        trailing_period_end="2026-06-30", total_debt=56_826 * M, total_cash=76_843 * M)
    kw.update(over)
    return Fundamentals(**kw)


def bond_source(ccy, *, today):
    return by.BondResult(ccy, by.BondYield(ccy, "US10Y", "US ten-year Treasury yield", 0.04934, "2026-11-02", 1))


def record(own5=0.172, own10=0.2116):
    leg = lambda v, w: Reading(value=v, span=w, label="x")
    return SimpleNamespace(eps=SimpleNamespace(cagr={5: leg(own5, 5), 10: leg(own10, 10)}))


def trend():
    return TrendData(current=TrendPeriod(period_end="2027-06-30", now=20.02, analysts=30),
                     next_year=TrendPeriod(period_end="2028-06-30", now=24.14, analysts=28))


def rule(key, label, reading, limit, result, note="", value=None):
    return RuleResult(key, label, reading, limit, result, note, value)


def gate_ok(*, warn=False) -> GateResult:
    rules = (rule("earnings_quality", "Earnings quality", "0.04 of assets", "excluded above 0.1", PASS),
             rule("solvency", "Solvency", "net cash", "excluded below 5.0%", PASS),
             rule("accounting_health", "Accounting health", "7 of 9", "excluded at 1 or lower", PASS),
             rule("cash_runway", "Cash runway", "not burning cash", "excluded under 12 months", PASS))
    warnings = (rule("highly_leveraged", "Highly leveraged", "net debt / EBITDA 7.2 times; cash flow over debt 9.0%",
                     "warning above 6 times", WARN if warn else PASS),
                rule("distress_score", "Distress score", "3.40", "warning below 1.10", PASS),
                rule("dividend_cover", "Dividend cover", "no dividend paid", "context", NOT_APPLICABLE),
                rule("runway_warning", "Cash runway", "not burning cash", "warning under 24 months", PASS))
    reason = ("Qualifies: no exclusion rule failed. Warnings: highly leveraged." if warn
              else "Qualifies: no exclusion rule failed. No warnings.")
    return GateResult("qualifies", reason, rules, warnings, debt_line="Debt = borrowings plus the long-term "
                      "operating lease line, from the balance sheet of Sep 2026.", definition_id="absolute_gate_v2")


def gate_excluded() -> GateResult:
    rules = (rule("earnings_quality", "Earnings quality", "0.14 of assets", "excluded above 0.1", FAIL, value=0.14),
             rule("solvency", "Solvency", "net cash", "excluded below 5.0%", PASS),
             rule("accounting_health", "Accounting health", "6 of 9", "excluded at 1 or lower", PASS),
             rule("cash_runway", "Cash runway", "not burning cash", "excluded under 12 months", PASS))
    return GateResult("excluded", "Excluded: Earnings quality (0.14 of assets, excluded above 0.1).", rules, (),
                      definition_id="absolute_gate_v2")


def gate_bank() -> GateResult:
    return GateResult("not_covered", "Not covered: banks and insurers are outside this gate (Financial Services).",
                      definition_id="absolute_gate_v2")


def gate_loss_maker() -> GateResult:
    missing = ("solvency and cash runway (a loss-maker before working capital; the runway is not evaluated: "
               "change in working capital missing)",)
    rules = (rule("earnings_quality", "Earnings quality", "0.02 of assets", "excluded above 0.1", PASS),
             rule("solvency", "Solvency", "not evaluated", "excluded below 5.0%", NOT_EVALUATED, "loss-maker, see runway"),
             rule("accounting_health", "Accounting health", "5 of 9", "excluded at 1 or lower", PASS),
             rule("cash_runway", "Cash runway", "not evaluated", "excluded under 12 months", NOT_EVALUATED,
                  "change in working capital missing"))
    return GateResult("not_enough_data", "Not enough data: " + "; ".join(missing) + ".", rules, (), missing,
                      definition_id="absolute_gate_v2")


def absolute(gate, *, price=521.91, f=None):
    return build_absolute(f if f is not None else msft(), gate=gate, last_close=price, growth_record=record(),
                          trend=trend(), today=TODAY, bond_source=bond_source)


def fake_report(gate, av, *, votes=(), outside=False):
    check = SimpleNamespace(gate=gate, absolute=av, company_name="Microsoft Corporation", analyst_trend=None,
                            valuation_band="not evaluated — fixture", band_percentile=None, providers={},
                            peer_group=None, debt_and_cash=None, growth_record=None)
    return SimpleNamespace(ticker="MSFT", check=check, votes=list(votes), agreement=None, no_vote_reason="",
                           outside_tested_range=outside, display="Microsoft Corporation (MSFT)", summary=None,
                           judge_panel=None, report_votes=None)


STATES = {
    "below": lambda: (gate_ok(), absolute(gate_ok())),
    "excluded": lambda: (gate_excluded(), absolute(gate_excluded())),
    "bank": lambda: (gate_bank(), absolute(gate_bank(), f=msft(sector="Financial Services"))),
    "loss maker": lambda: (gate_loss_maker(), absolute(gate_loss_maker())),
    "clears": lambda: (gate_ok(), absolute(gate_ok(), price=100.0)),
}


def case(name):
    gate, av = STATES[name]()
    return fake_report(gate, av)


# --------------------------------------------------------------------------- #
# section 1: the answer, for every state
# --------------------------------------------------------------------------- #
def test_the_fixtures_reach_the_five_verdict_states():
    got = {k: v()[1].verdict.state for k, v in STATES.items()}
    assert got == {"below": BELOW_HURDLE, "excluded": EXCLUDED, "bank": NOT_COVERED,
                   "loss maker": NOT_ENOUGH_DATA, "clears": CLEARS_HURDLE}


def test_microsoft_below_hurdle_reads_the_design_pages_range_and_hurdle():
    first, second = cp.answer_lines(case("below"))
    assert first == ("Qualifies, below hurdle: the price returns 4.6% to 8.6% a year if growth holds, against a "
                     "hurdle of 9.93%.")
    assert "24.0% a year of growth in free cash flow per share for ten years" in second
    assert "0 of 4 cases clear" in second


def test_an_exclusion_names_the_rule_its_figure_and_its_limit():
    first, second = cp.answer_lines(case("excluded"))
    assert first == "Excluded: earnings quality reads 0.14 of assets, past the limit of 0.1."
    assert "fails a check on its accounts or its debt" in second


def test_a_bank_is_not_covered():
    first, second = cp.answer_lines(case("bank"))
    assert first == "Not covered: banks and insurers are outside this method."
    assert "bank or an insurer" in second


def test_a_loss_maker_lists_the_missing_items_as_not_enough_data():
    first, second = cp.answer_lines(case("loss maker"))
    assert first.startswith("Not enough data: solvency and cash runway (a loss-maker before working capital")
    assert first.endswith(".")
    assert "neither a pass nor a fail" in second


def test_a_company_that_clears_says_above_the_hurdle():
    rep = case("clears")
    av = rep.check.absolute
    first, second = cp.answer_lines(rep)
    lo, hi = 100 * av.verdict.low, 100 * av.verdict.high
    assert first == (f"Qualifies, clears hurdle: the price returns {lo:.1f}% to {hi:.1f}% a year if growth holds, "
                     "above the hurdle of 9.93%.")
    assert f"{av.verdict.of} of {av.verdict.of} cases clear" in second


def test_no_gate_and_no_price_test_is_not_enough_data_never_a_crash():
    first, _second = cp.answer_lines(fake_report(None, None))
    assert first.startswith("Not enough data:")


# --------------------------------------------------------------------------- #
# section 2: the story, five paragraphs, for every state
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", list(STATES))
def test_the_story_has_the_five_leads_in_order_and_says_no_banned_word(name):
    rep = case(name)
    paras = cp.story_paragraphs(rep)
    assert [lead for lead, _ in paras] == list(cp.LEADS) == [
        "What was asked.", "What the gate found.", "What the price requires.", "What to doubt.",
        "What this cannot tell you."]
    assert all(text.strip() for _lead, text in paras)
    blob = " ".join(cp.answer_lines(rep)) + " " + " ".join(t for _l, t in paras)
    assert not BANNED.search(blob), BANNED.search(blob).group(0)
    assert "cohort" not in blob.lower() and "strategy" not in blob.lower()


def test_the_gate_paragraph_gives_every_exclusion_result_and_names_the_warnings():
    paras = dict(cp.story_paragraphs(fake_report(gate_ok(warn=True), absolute(gate_ok(warn=True)))))
    found = paras["What the gate found."]
    for label in ("Earnings quality", "solvency", "accounting health", "cash runway"):
        assert label.lower() in found.lower()
    assert found.count("passes") == 4
    assert "Warnings fired: highly leveraged." in found
    assert "Warnings: highly leveraged (net debt / EBITDA 7.2 times" in paras["What to doubt."]
    assert "No warning fired." in dict(cp.story_paragraphs(case("below")))["What the gate found."]


def test_the_excluded_story_says_which_rule_failed():
    found = dict(cp.story_paragraphs(case("excluded")))["What the gate found."]
    assert "Earnings quality fails (0.14 of assets, limit 0.1)" in found


def test_the_price_paragraph_for_microsoft_carries_the_base_the_implied_growth_and_the_count():
    price = dict(cp.story_paragraphs(case("below")))["What the price requires."]
    assert "$9.64 of free cash flow per share (the latest year)" in price
    assert "needs 24.0% a year of growth for ten years" in price
    assert "17.2% (5-year)" in price and "21.2% (10-year)" in price
    assert "above the 18% a year few companies have kept up" in price
    assert "own 5-year growth 6.8%" in price and "the downside case 4.6%" in price
    assert price.rstrip().endswith("0 of 4 cases clear.")


def test_the_doubt_paragraph_names_the_untested_consensus_the_marks_and_the_evidence_line():
    doubt = dict(cp.story_paragraphs(case("below")))["What to doubt."]
    assert "no past data can test" in doubt
    assert "Marked above 18% a year: own 10-year growth" in doubt
    assert "a question put to the reader and not an answer" in doubt          # the section f evidence line
    outside = dict(cp.story_paragraphs(fake_report(gate_ok(), absolute(gate_ok()), outside=True)))
    assert "smaller than the range the checks were tested on" in outside["What to doubt."]


def test_the_cannot_paragraph_says_no_forecast_no_advice_the_backtest_form_and_no_backtest():
    text = dict(cp.story_paragraphs(case("below")))["What this cannot tell you."]
    assert "not a forecast and not advice" in text
    assert "plans, its management or today's news" in text
    assert "Qualifies, below hurdle, 0 of 3 cases clear" in text
    assert "No backtest of this method exists yet" in text


def test_a_banks_story_does_not_invent_a_price_test():
    paras = dict(cp.story_paragraphs(case("bank")))
    assert paras["What the price requires."] == "No price test is worked out for a bank or an insurer."
    assert "outside this gate" in paras["What the gate found."]


# --------------------------------------------------------------------------- #
# sections 3 to 5: the gate table, the price test, the return range (no phase labels)
# --------------------------------------------------------------------------- #
def test_the_section_titles_carry_no_phase_label():
    assert grender.SECTION_TITLE == "The gate"
    titles = [t for _k, t, _c, _b in vrender.sections(case("below").check.absolute)]
    assert titles == ["The price test", "The return range"]
    blob = " ".join(grender.text_lines(gate_ok()) + vrender.text_lines(case("below").check.absolute))
    assert "phase" not in blob.lower() and "ABSOLUTE-1" not in blob


def test_a_bank_has_a_price_test_note_and_no_return_range_section():
    secs = vrender.sections(case("bank").check.absolute)
    assert [k for k, *_ in secs] == ["price test"]


def test_the_return_range_section_holds_the_cases_the_hurdle_the_verdict_and_the_backtest_form():
    lines = "\n".join(vrender.text_lines(case("below").check.absolute))
    assert "THE RETURN RANGE" in lines
    assert "Hurdle: 9.93% = US ten-year Treasury yield 4.93%" in lines
    assert "Verdict: Qualifies, below hurdle, 0 of 4 cases clear." in lines
    assert "Backtest form" in lines and "0 of 3 cases clear" in lines


# --------------------------------------------------------------------------- #
# section 6: context, no vote
# --------------------------------------------------------------------------- #
def _vote(label, status="ranked", verdict="buy", position=3, size=14, reason=""):
    return LensVote(strategy_id="x_v1", label=label, status=status, verdict=verdict, position=position,
                    cohort_size=size, reason=reason)


def test_where_it_sits_among_rivals_is_one_line_per_lens_with_a_rank_and_no_vote_word():
    rep = fake_report(gate_ok(), absolute(gate_ok()), votes=[
        _vote("Value + Momentum", verdict="buy", position=3, size=14),
        _vote("Magic Formula RAW", verdict="sell", position=13, size=14),
        _vote("Defensive Income", status="excluded", reason="no dividend paid")])
    lines = cp.rivals_lines(rep)
    assert lines == ["Value + Momentum: 3rd of 14.", "Magic Formula RAW: 13th of 14.",
                     "Defensive Income: not ranked (no dividend paid)."]
    assert not BANNED_CASE.search(" ".join(lines))


def test_the_context_carries_the_quality_trough_year_and_the_momentum_and_no_vote_word():
    av = absolute(gate_ok())
    av.context.update({"worst_roic": 0.259, "worst_roic_year": "the year to Jun 2023", "gross_profitability": 0.297,
                       "momentum_12m": 0.014, "momentum_6m": 0.272})
    rep = fake_report(gate_ok(), av, votes=[_vote("Value + Momentum")])
    text = "\n".join(cp.context_text_lines(rep))
    assert text.startswith("CONTEXT, NO VOTE")
    assert "Worst year of return on invested capital in the last five years: 25.9% (the year to Jun 2023)." in text
    assert "Gross profit over total assets: 29.7%." in text
    assert "The share price moved +1% over twelve months and +27% over six." in text
    assert "Where it sits among rivals" in text
    assert not BANNED_CASE.search(text) and "target" not in text.lower()


# --------------------------------------------------------------------------- #
# the page order, in the text, the Markdown and the HTML
# --------------------------------------------------------------------------- #
def _order(text, headings):
    low = text.lower()
    at = [low.find(h.lower()) for h in headings]
    assert all(i >= 0 for i in at), dict(zip(headings, at))
    assert at == sorted(at), dict(zip(headings, at))


@pytest.fixture(scope="module")
def full_report(tmp_path_factory):
    from tests.test_company_report import _run
    from aristos_council.judges.panel import PanelResult
    report = _run(["magic_formula_raw_v1"], tmp_path=tmp_path_factory.mktemp("page"))
    report.judge_panel = PanelResult(ticker="CO", available=False, note="fixture: no key")
    return report


TEXT_HEADINGS = ["What was asked.", "THE GATE", "THE PRICE TEST", "THE RETURN RANGE", "CONTEXT, NO VOTE",
                 "JUDGE PANEL", "SHOW THE WORKINGS", "RELATIVE VIEW (PEER RANKS): NO LONGER VOTES"]
PAGE_HEADINGS = ["The answer", "The story", "The gate", "The price test", "The return range", "Context, no vote",
                 "Judge panel", "Show the workings", "Relative view (peer ranks): no longer votes"]


def test_the_text_download_follows_the_new_page_order(full_report):
    text = format_company_report(full_report)
    _order(text, TEXT_HEADINGS)


def test_the_markdown_download_follows_the_new_page_order(full_report):
    _order(company_report_markdown(full_report), PAGE_HEADINGS)


def test_the_html_download_follows_the_new_page_order(full_report):
    _order(visible_text(company_report_html(full_report)), PAGE_HEADINGS)


def test_the_answer_comes_before_the_story_and_the_lens_table_is_only_in_the_workings(full_report):
    text = format_company_report(full_report)
    first_lines = [ln for ln in text.splitlines() if ln.strip()][2:4]
    assert first_lines == list(cp.answer_lines(full_report))
    head = text[:text.lower().index("show the workings")]
    assert "Track record" not in head and "Vote or mark" not in head
    assert "Vote or mark" in text[text.lower().index("show the workings"):]


def test_the_page_order_constant_lists_the_sections_in_this_order():
    from aristos_council.company_report import SECTION_ORDER
    assert SECTION_ORDER[:9] == ("answer", "story", "gate", "price test", "return range", "context",
                                 "judge panel", "council opinion", "workings")


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #
OLD_KEYS = {"schema_version", "kind", "ticker", "company", "run_at", "lenses", "peer_snapshot", "peer_sentence",
            "universe", "votes", "agreement", "no_vote_reason", "cohort_slug", "track_record_caption",
            "track_record_summary", "lens_ranks", "sources", "seconds", "cache", "summary_written",
            "council_opinion", "outside_tested_range", "smallcap_cohort", "smallcap_floor_usd",
            "smallcap_band_note", "size_matched_peers", "price_and_cash"}


def test_a_new_record_carries_the_absolute_verdict_the_relative_result_and_the_design_version(full_report):
    rec = report_record(full_report)
    json.dumps(rec, default=str)                                   # serialisable
    assert OLD_KEYS <= set(rec)                                    # every older key is still there
    assert rec["design_version"].startswith("ABSOLUTE-1 phase 3")
    v = rec["verdict_of_record"]
    assert v["state"] in {"excluded", "not_covered", "not_enough_data", "qualifies_below_hurdle",
                          "qualifies_clears_hurdle", ""}
    assert v["design"] == rec["design_version"] and len(v["answer"]) == 2
    assert "relative" in rec and rec["relative"]["note"].startswith("relative view")
    assert rec["agreement"] is not None and "headline" in rec["agreement"]         # the old key, unchanged


def test_the_relative_result_is_kept_apart_from_the_verdict_of_record():
    rep = fake_report(gate_ok(), absolute(gate_ok()))
    assert cp.verdict_record(rep)["state"] == BELOW_HURDLE
    assert cp.relative_record(rep)["verdict"] == "" and "no_vote_reason" in cp.relative_record(rep)


def test_saving_a_new_run_never_rewrites_a_file_already_on_disk(full_report, tmp_path):
    old = tmp_path / "2026-10-01T09-00-00Z_company_check_OLD"
    old.mkdir()
    old_json = '{"schema_version": 2, "kind": "company_report", "ticker": "OLD", "votes": [], "agreement": null}'
    (old / "report.json").write_text(old_json, encoding="utf-8")
    (old / "report.txt").write_text("old text", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    folder = save_company_report(full_report, tmp_path)
    assert folder != old and (folder / "report.json").exists()
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before
    assert json.loads((old / "report.json").read_text(encoding="utf-8")).get("verdict_of_record") is None
    assert "verdict_of_record" in json.loads((folder / "report.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# the summary (reader): its facts pack and its checker are re-pointed at the new page
# --------------------------------------------------------------------------- #
def _facts_report(*, warn=True, excluded=False):
    gate = gate_excluded() if excluded else gate_ok(warn=warn)
    return fake_report(gate, absolute(gate))


def _good_summary():
    return dict(
        asked="Would a buyer of Microsoft at today's price, holding for ten years, more likely than not beat the "
              "ten-year US bond yield plus 5 points?",
        happened="The gate found no failed exclusion. One warning fired: Highly leveraged.",
        survived="Free cash flow is 9.64 dollars a share. At the hurdle the price needs 24.0% growth a year for "
                 "ten years. The four cases return 4.6% to 8.6%, so 0 of 4 clear the hurdle. It qualifies, "
                 "below hurdle.",
        doubt="The consensus case rests on analyst forecasts that no past data can test.",
        cannot_say="This is not a forecast and no backtest of this method exists yet.")


def _check(fields, report=None):
    from aristos_council.company_report import company_facts_pack
    from aristos_council.reader_check import check_summary
    return check_summary(fields, company_facts_pack(report or _facts_report()))


def test_the_facts_pack_is_built_from_the_page_and_names_what_must_be_named():
    from aristos_council.company_report import company_facts_pack
    pack = company_facts_pack(_facts_report())
    assert pack["answer"] == list(cp.answer_lines(_facts_report()))
    assert pack["gate"]["to_name"] == ["Highly leveraged"]
    assert [c["role"] for c in pack["return_range"]["cases"]] == ["record", "record", "forecast", "downside"]
    assert any("24.0% a year of growth" in ln for ln in pack["price_test"]["lines"])
    assert any("Verdict: Qualifies, below hurdle, 0 of 4 cases clear." in ln for ln in pack["return_range"]["lines"])
    assert company_facts_pack(_facts_report(excluded=True))["gate"]["to_name"] == ["Earnings quality"]
    assert "lenses" not in pack and "agreement" not in pack


def test_a_summary_that_repeats_the_page_passes_all_four_checks():
    check = _check(_good_summary())
    assert check.ok, check.reason
    assert check.problems == []


def test_condition_1_a_number_not_in_the_pages_tables_withholds_the_summary():
    fields = _good_summary()
    fields["survived"] += " Its profit grew 777 percent."
    check = _check(fields)
    assert not check.ok and "number not in the facts: 777" in check.reason


def test_condition_2_a_company_not_on_the_page_withholds_the_summary():
    fields = _good_summary()
    fields["doubt"] += " Compare ACME."
    check = _check(fields)
    assert not check.ok and "name not in the run: ACME" in check.reason


def test_condition_3_a_warning_called_an_exclusion_withholds_the_summary():
    fields = _good_summary()
    fields["happened"] += " Highly leveraged is an exclusion that removes the company."
    check = _check(fields)
    assert not check.ok and "role mismatch: highly leveraged called an exclusion" in check.reason


def test_condition_3_a_growth_case_given_the_wrong_role_withholds_the_summary():
    for claim, expected in ((" The downside case is a forecast.", "downside case called a forecast"),
                            (" Analyst consensus is a record of its past.", "analyst consensus called a record"),
                            (" Own 10-year growth is a forecast.", "own 10-year growth called a forecast")):
        fields = _good_summary()
        fields["doubt"] += claim
        check = _check(fields)
        assert not check.ok and f"role mismatch: {expected}" in check.reason, (claim, check.reason)


def test_a_role_used_rightly_is_not_a_mismatch():
    fields = _good_summary()
    fields["happened"] += " Highly leveraged is a warning, and the downside case is a stress path."
    assert _check(fields).ok


def test_condition_4_a_warning_that_fired_must_be_named():
    fields = _good_summary()
    fields["happened"] = "The gate found no failed exclusion and no warning worth a mention."
    check = _check(fields)
    assert not check.ok and "exclusion or warning not mentioned: Highly leveraged" in check.reason


def test_condition_4_an_exclusion_that_failed_must_be_named():
    fields = dict(_good_summary(), happened="The gate found a problem with the accounts.",
                  survived="No return is relied on.")
    excluded = _facts_report(excluded=True)
    check = _check(fields, excluded)
    assert not check.ok and "exclusion or warning not mentioned: Earnings quality" in check.reason
    fields["happened"] = "The gate excluded it: earnings quality failed."
    assert _check(fields, excluded).ok, _check(fields, excluded).reason


def test_the_summary_replaces_the_story_and_uses_the_same_five_leads():
    from aristos_council.reader import COMPANY_LEADS, reader_paragraphs
    from aristos_council.agents.schemas import ReaderSummary
    paras = reader_paragraphs(ReaderSummary(**_good_summary()), company=True)
    assert tuple(lead for lead, _t in paras) == COMPANY_LEADS == cp.LEADS
