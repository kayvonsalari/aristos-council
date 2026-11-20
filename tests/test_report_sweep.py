"""REPORT-SWEEP-1 - the automatic report checker, run over an awkward set of companies and lists.

Fixtures: ``tests/fixtures/report_sweep/`` (recorded once from a live run by
``scripts/record_report_sweep_fixtures.py``; replayed here through ``FrozenAdapter``, so nothing in
this module touches the network). The set:

    F (loss-maker)   VKTX (pre-revenue biotech)   NVCR.US (small cap)   1211.HK (foreign reporting
    currency)   JPM (bank)   an ETF list SPY, SCHD, VWRL.L, AAPL   stock lists of 1, 3 and 6 names

For each, the company or list report is built and every export the code makes is checked: the screen
text, the markdown (where streamlit is installed - the markdown builders live in app.py) and the
HTML. One test per RULE, aggregated over the whole set, so a rule fails with every place it was
found.

BATCH 18B cleared the three rules that were expected failures in 18A (internal ids, "$-" money,
count grammar): they are ordinary tests now, and a new leak of any of them fails CI.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import report_sweep as sweep
from aristos_council.company_markdown import company_report_markdown
from aristos_council.company_report import (format_company_report, run_company_report)
from aristos_council.export.report_html import (company_report_html, multi_strategy_report_html,
                                                universe_report_html)
from aristos_council.market_index import IndexRow
from aristos_council.persistence.replay import FrozenAdapter
from aristos_council.pipeline import (format_cli_report, format_multi_strategy_grid,
                                      run_multi_strategy_pipeline, run_rank_pipeline)

FIX = Path(__file__).resolve().parent / "fixtures" / "report_sweep"
META = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
TODAY = date.fromisoformat(META["recorded_on"])
RUN_START = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def load(self):
        return list(self._rows)


def _no_news(ticker, *, today):
    from aristos_council.data.news_fallback import NewsFetchResult
    return NewsFetchResult(items=(), source="", tried=("sweep fixtures: no news recorded",))


def _markdown_builders():
    """The markdown exports live in app.py (they import streamlit). Absent in the CI image, so the
    markdown column of the sweep runs wherever streamlit is installed and is skipped elsewhere."""
    try:
        import app
    except Exception:                                           # noqa: BLE001
        return None
    return app


@pytest.fixture(scope="module")
def sweep_reports(tmp_path_factory):
    return build_sweep(tmp_path_factory.mktemp("sweep_runs"))


def build_sweep(runs):
    """Every report in the set, built once: ``{name: {"exports": {kind: text}, "company": report |
    None, "multi": ..., "single": ...}}``. A plain function so scripts can reuse it."""
    adapter = FrozenAdapter(FIX / "frozen")
    rows = json.loads((FIX / "index_rows.json").read_text(encoding="utf-8"))
    app_mod = _markdown_builders()
    out: dict = {}

    for ticker in META["companies"]:
        store = _Store([IndexRow(**r) for r in rows[ticker]])
        report = run_company_report(
            ticker, META["stock_lenses"], adapter=adapter, store=store, today=TODAY,
            include_small=ticker in META["small"], save=False, news_fetcher=_no_news,
            runs_dir=runs)
        out[f"company {ticker}"] = {
            "company": report, "multi": None, "single": None,
            "exports": {"text": format_company_report(report),
                        "html": sweep.visible_text(company_report_html(report)),
                        "company md": company_report_markdown(report)}}

    def list_case(label, names, lenses):
        multi = run_multi_strategy_pipeline(names, lenses, adapter=adapter, today=TODAY,
                                            use_cache=False, freeze_dir=runs)
        single = run_rank_pipeline(names, lenses[0], ranker_only=True, adapter=adapter,
                                   today=TODAY, use_cache=False, freeze_dir=runs)
        exports = {
            "multi text": format_multi_strategy_grid(multi),
            "multi html": sweep.visible_text(multi_strategy_report_html(multi, run_start=RUN_START)),
            "single text": format_cli_report(single),
            "single html": sweep.visible_text(universe_report_html(single, run_start=RUN_START)),
        }
        if app_mod is not None:
            exports["multi md"] = app_mod._multi_strategy_markdown(multi, RUN_START)
            exports["single md"] = app_mod._universe_markdown(single)
        out[label] = {"company": None, "multi": multi, "single": single, "exports": exports}

    for size in (1, 3, 6):
        list_case(f"stock list of {size}", META["car_list"][:size], META["list_lenses"])
    list_case("ETF list", META["etf_list"], META["etf_lenses"])
    return out


def _each_export(reports):
    for name, case in reports.items():
        for kind, text in case["exports"].items():
            yield f"{name} / {kind}", kind, text


def _text_rule(reports, check):
    found = []
    for where, kind, text in _each_export(reports):
        found += check(text, where, kind)
    return found


def _assert_clean(findings, rule):
    assert not findings, f"{len(findings)} finding(s) for '{rule}':\n" + "\n".join(
        f"  {f}" for f in findings[:12])


# --------------------------------------------------------------------------- #
# the sweep itself ran
# --------------------------------------------------------------------------- #
def test_the_sweep_built_every_report_in_the_set(sweep_reports):
    assert set(sweep_reports) == {f"company {t}" for t in META["companies"]} | {
        "stock list of 1", "stock list of 3", "stock list of 6", "ETF list"}
    for name, case in sweep_reports.items():
        for kind, text in case["exports"].items():
            assert len(text) > 400, f"{name} / {kind} is suspiciously short"
    f = sweep_reports["company F"]["company"]
    assert f.votes, "the company page ran no lens"


def test_the_markdown_half_of_the_sweep_runs_in_ci(sweep_reports):
    """CI installs the ``ui`` extra (streamlit) precisely so the markdown exports - which live in
    app.py - are swept there too. Where streamlit is simply not installed (a bare dev checkout) the
    markdown half is skipped, but on CI a missing markdown export is a failure, not a skip."""
    import os
    if not os.environ.get("CI"):
        pytest.skip("markdown half is optional outside CI")
    for name, case in sweep_reports.items():
        if case["multi"] is not None:
            assert "multi md" in case["exports"] and "single md" in case["exports"], name


def test_the_set_is_actually_awkward(sweep_reports):
    """A sweep over easy companies proves nothing: pin what makes each one awkward."""
    f = sweep_reports["company F"]["company"]
    assert f.check.company_name or f.display
    jpm = sweep_reports["company JPM"]["company"]
    assert jpm.cohort_slug is None                              # a bank: no backtested cohort
    vktx = sweep_reports["company VKTX"]["company"]
    assert vktx.outside_tested_range is True                    # under $5bn, include-small on
    etf = sweep_reports["ETF list"]["multi"]
    assert any(reason.startswith("asset kind") for res in etf.results.values()
               for _t, reason in res.excluded)                  # the stock in the fund list
    assert len(sweep_reports["stock list of 1"]["multi"].rows) == 1


# --------------------------------------------------------------------------- #
# Rules cleared by Batch 18B (they were xfail(strict) in 18A) - now ordinary tests
# --------------------------------------------------------------------------- #
def test_no_internal_ids_in_reader_text(sweep_reports):
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.internal_id_findings(t, w)),
                  sweep.INTERNAL_IDS)


def test_no_dollar_minus_money_formatting(sweep_reports):
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.money_minus_findings(t, w)),
                  sweep.MONEY_MINUS)


def test_no_count_grammar_slips(sweep_reports):
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.count_grammar_findings(t, w)),
                  sweep.COUNT_GRAMMAR)


def test_no_batch22_wording_slips(sweep_reports):
    """Batch 22: the wording patterns found by hand (one lens "all for one reason", "(tied with 1)",
    a lowercase sentence start after a full stop, "What survived.", peer-search jargon in the story,
    backtest jargon in the lens notes) never come back."""
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.wording_findings(t, w)),
                  sweep.WORDING)


def test_the_batch22_wording_rule_fires_on_each_shape_it_names():
    for bad in _B22_BAD:
        assert sweep.wording_findings(bad + FOLD_B22, "x"), bad
    for ok in _B22_OK:
        assert not sweep.wording_findings(ok + FOLD_B22, "x"), ok


FOLD_B22 = "\nSHOW THE WORKINGS"
_B22_BAD = ["matched its group · 0 of 0 years positive · no random-pick comparison · 0 monthly test rounds",
            "Forensic doubted - 9th of 16 ranked on 1 of 3 factors",
            "not evaluated - multiple implausible (265x); inputs suspect, not stated", "1 name had no BUY from any lens, and are not listed here.",
            "a - falling over both windows - sustained weakness. b - falling over both windows - sustained weakness.",
            "beat its group by +2.6% a year on average - In this cohort, this lens's record does not clear the bar, and does no better than picking names at random.",
            "Narration check: 1 statement in the council opinion was flagged.",
            "The answer\nWhat happened. 14 of 20 had usable figures for Quality.\nLens by lens\n14 of 20 had usable figures for Quality.",
            "beat its group by -1.1% a year on average",
            "The answer\nNo track record exists for this industry yet.\nWhat this cannot tell you. x. No track record exists for this industry yet.",
            "the rule allows at most 1.0x. on its measures it would rank 6th", "Quality track record: mean excess -1.1%/yr " + chr(183) + " luck 51% " + chr(183) + " 108 rounds held",
            "HOLD - 19th of 27 (tied with 1)",
            "The answer\nBYD was ranked against 19 peers, companies in its own industry (step 2 of 4 of the peer search, market index of 2026-09-25).",
            "One lens did not apply, all for one reason: no operating profit.",
            "The answer\nWhat survived. Debt and cash (latest annual accounts): it owes $600."]
_B22_OK = ["No test history for this lens in this industry yet.", "beat its group by +4.5% a year on average · 7 of 10 years positive · 108 monthly test rounds",
           "Forensic doubted (on one test only; two had no data) - 9th of 16 ranked on 1 of 3 factors", "Forensic doubted - 9th of 16 ranked on 3 of 3 factors",
           "not read: the earnings figure looks unreliable", "1 name had no BUY from any lens, and is not listed here.", "2 names had no BUY from any lens, and are not listed here.",
           "a - falling over both windows - sustained weakness. b - rising over both windows - a sustained advance.",
           "beat its group by +2.6% a year on average - Fell short on the winning years (only 5 of 10; the bar is 6).",
           "AI text check: no issues found",
           "Lens by lens\n14 of 20 had usable figures for Quality; 20 of 20 for Forensic.",
           "trailed its group by 1.1% a year on average", "beat its group by +4.5% a year on average",
           "the rule allows at most 1.0x. On its measures it would rank 6th", "trailed its group by 1.1% a year on average",
           "HOLD - 19th of 27 (tied with one other)", "HOLD - 19th of 27 (tied with 10)",
           "The answer\nBYD was ranked against 19 similar-sized companies in its industry, under nine lenses.",
           "SHOW THE WORKINGS\n(step 2 of 4 of the peer search)",
           "One lens did not apply: no operating profit.",
           "The answer\nOther facts. Debt and cash (latest annual accounts): it owes $600.",
           "What survived. Two companies were rated BUY by both voting tests.",
           "Five lenses did not apply, all for one reason: no operating profit."]


def test_no_strategy_word_in_reader_text(sweep_reports):
    """19B B3: "lens" everywhere, never "strategy"."""
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.strategy_word_findings(t, w)),
                  sweep.STRATEGY_WORD)


def test_no_badge_paragraph_outside_the_glossary(sweep_reports):
    """19B B10: the five-sentence price-badge explanation lives in the glossary only."""
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.badge_paragraph_findings(t, w)),
                  sweep.BADGE_PARAGRAPH)


def test_company_story_sections_carry_no_id_column_name_cohort_or_strategy(sweep_reports):
    """COMPANY-STORY-1: sections 1-3 (the answer, the story, the lens table) of a company page, in
    the text, the HTML and the Markdown, name a lens by its display name and say neither "cohort"
    nor "strategy" nor a column's name."""
    found = []
    kinds = set()
    for where, kind, text in _each_export(sweep_reports):
        if where.startswith("company "):
            kinds.add(kind)
            found += sweep.story_findings(text, where)
    assert kinds == {"text", "html", "company md"}
    _assert_clean(found, sweep.STORY_WORDS)


def test_the_first_screen_of_every_company_report_is_at_most_25_lines(sweep_reports):
    found = []
    for where, kind, text in _each_export(sweep_reports):
        if where.startswith("company ") and kind == "text":
            found += sweep.first_screen_findings(text, where)
    _assert_clean(found, sweep.STORY_FIRST_SCREEN)


def test_the_story_rules_fire_on_the_shapes_they_name():
    fold = "\nSHOW THE WORKINGS\ncohort magic_formula_raw_v1 strategy are fine below the fold\n"
    assert not sweep.story_findings("The answer is HOLD." + fold, "x")
    assert sweep.story_findings("Track record from the Autos cohort." + fold, "x")
    assert sweep.story_findings("Ranked by magic_formula_raw_v1." + fold, "x")
    assert sweep.story_findings("The strategy voted." + fold, "x")
    assert sweep.story_findings("BUY votes: 1" + fold, "x")
    assert sweep.first_screen_findings("\n".join(["line"] * 30) + fold, "x")
    assert not sweep.first_screen_findings("\n".join(["line"] * 20) + fold, "x")


# --------------------------------------------------------------------------- #
# ABSOLUTE-PAGE-1: the page built around the absolute verdict
# --------------------------------------------------------------------------- #
def _company_exports(reports):
    for where, kind, text in _each_export(reports):
        if where.startswith("company "):
            yield where, kind, text, reports[where.split(" / ")[0]]["company"]


def test_sections_1_to_5_name_no_lens_id_column_or_label(sweep_reports):
    """The answer, the story, the gate, the price test and the return range, in all four renderers (the text, the
    HTML and the Markdown here; the Streamlit page reads the same blocks), name no lens - by id, by column name or
    by display label. A lens may be named only from section 6 on."""
    found, kinds = [], set()
    for where, kind, text, report in _company_exports(sweep_reports):
        kinds.add(kind)
        # a one-word label that is also plain English ("Growth", "Quality") cannot be told from the word itself, so
        # only the labels that are unmistakably a lens's name are matched (the same limit the id rule documents)
        labels = [v.label for v in report.votes if " " in v.label or v.label == "Forensic"]
        found += sweep.page_lens_findings(text, where, labels)
    assert kinds == {"text", "html", "company md"}
    _assert_clean(found, sweep.PAGE_LENS_NAMES)


def test_sections_1_to_6_never_say_expected_return_target_proven_buy_hold_or_sell(sweep_reports):
    found = []
    for where, kind, text, _report in _company_exports(sweep_reports):
        found += sweep.page_banned_word_findings(text, where)
    _assert_clean(found, sweep.PAGE_BANNED_WORDS)


def test_the_sections_are_found_where_the_page_puts_them(sweep_reports):
    """The two section cuts the rules above rest on really split the page: the context heading is there, the
    relative view is below the fold, and the vote words live only below section 6."""
    for where, kind, text, report in _company_exports(sweep_reports):
        low = text.lower()
        assert "context, no vote" in low, where
        assert "relative view (peer ranks): no longer votes" in low, where
        head5, head6 = sweep.sections_1_to_5(text), sweep.sections_1_to_6(text)
        assert len(head5) < len(head6) < len(text), where
        assert "relative view" not in head6.lower(), where


def test_the_new_page_rules_fire_on_the_shapes_they_name():
    ctx = "\nCONTEXT, NO VOTE\nWhere it sits among rivals:\nMagic Formula RAW: 4th of 13.\n"
    fold = "SHOW THE WORKINGS\nHOLD by majority: two of two votes say HOLD (Magic Formula RAW).\n"
    ok = "The answer\nQualifies, below hurdle: the price returns 4.3% to 8.1% a year if growth holds." + ctx + fold
    assert not sweep.page_lens_findings(ok, "x", ["Magic Formula RAW"])
    assert not sweep.page_banned_word_findings(ok, "x")
    # a lens label in section 3 is a finding; in section 6 it is not
    assert sweep.page_lens_findings("The story\nMagic Formula RAW ranks it 4th." + ctx + fold, "x",
                                    ["Magic Formula RAW"])
    assert sweep.page_lens_findings("The story\nranked by magic_formula_raw_v1" + ctx + fold, "x")
    assert sweep.page_lens_findings("The story\nBUY votes: 2" + ctx + fold, "x")
    # the banned words, in section 5 or in section 6
    for bad in ("an expected return of 7%", "the analysts' target is $5", "a proven method", "rated BUY",
                "a HOLD", "then SELL"):
        assert sweep.page_banned_word_findings(f"The answer\n{bad}\n" + ctx + fold, "x"), bad
        assert sweep.page_banned_word_findings("The answer\nfine.\n" + ctx.replace("Magic", bad + " Magic") + fold,
                                               "x"), bad
    # ...and below the context they are allowed (the relative view and the judges' answers)
    assert not sweep.page_banned_word_findings("The answer\nfine.\n" + ctx + fold + "target proven BUY\n", "x")


def test_the_multi_lens_progress_line_names_lenses_not_ids(tmp_path):
    """19B B2: "Grading with magic_formula_momentum_v1 (1 of 9)" put a record key on the screen."""
    adapter = FrozenAdapter(FIX / "frozen")
    lines: list[str] = []
    run_multi_strategy_pipeline(META["car_list"][:3], META["list_lenses"], adapter=adapter,
                                today=TODAY, use_cache=False, freeze_dir=tmp_path,
                                progress=lines.append)
    graded = [ln for ln in lines if ln.startswith("Grading with")]
    assert len(graded) == len(META["list_lenses"])
    for ln in graded:
        assert not sweep.internal_id_findings(ln, "progress"), ln
    assert "Grading with Value + Momentum (3 of 3)" in graded[-1] or any(
        "Value + Momentum" in ln for ln in graded)


# --------------------------------------------------------------------------- #
# Rules Batch 18A owns - these must pass for real
# --------------------------------------------------------------------------- #
def test_no_unmatched_bold_or_stray_underscore(sweep_reports):
    _assert_clean(_text_rule(
        sweep_reports,
        lambda t, w, k: sweep.markup_findings(t, w, markdown=k.endswith("md"))),
        sweep.UNMATCHED_MARKUP)


def test_no_backwards_size_band(sweep_reports):
    _assert_clean(_text_rule(sweep_reports, lambda t, w, k: sweep.backwards_band_findings(t, w)),
                  sweep.BACKWARDS_BAND)


def _lens_results(case):
    """``(label, ranked rows)`` for every lens in a case, from the structured results."""
    out = []
    if case["company"] is not None:
        for sid, record in (case["company"].lens_ranks or {}).items():
            rows = [SimpleNamespace(verdict=r["verdict"], excluded=False)
                    for r in record.get("ranked", [])]
            out.append((sid, rows))
    for key in ("multi",):
        if case[key] is not None:
            for sid, res in case[key].results.items():
                out.append((sid, res.ranked))
    if case["single"] is not None:
        out.append((case["single"].meta.get("rank_strategy_id", "single"), case["single"].ranked))
    return out


def test_no_lens_gives_its_top_a_buy_and_its_bottom_no_sell(sweep_reports):
    found = []
    for name, case in sweep_reports.items():
        for label, ranked in _lens_results(case):
            found += sweep.buy_without_sell_findings(label, ranked, where=name)
    _assert_clean(found, sweep.BUY_WITHOUT_SELL)


def test_sell_votes_are_in_the_agreement(sweep_reports):
    found = []
    for name, case in sweep_reports.items():
        if case["company"] is not None:
            found += sweep.company_agreement_findings(case["company"], name)
        if case["multi"] is not None:
            found += sweep.list_agreement_findings(case["multi"], name)
    _assert_clean([f for f in found if f.rule == sweep.SELL_MISSING], sweep.SELL_MISSING)


def test_a_lens_is_stated_one_way_in_every_section(sweep_reports):
    found = []
    for name, case in sweep_reports.items():
        if case["company"] is not None:
            found += sweep.company_agreement_findings(case["company"], name)
            found += sweep.peers_table_findings(case["company"], name)
        if case["multi"] is not None:
            found += sweep.list_agreement_findings(case["multi"], name)
    _assert_clean([f for f in found if f.rule == sweep.LENS_TWO_WAYS], sweep.LENS_TWO_WAYS)


# --------------------------------------------------------------------------- #
# the rules themselves catch what they say they catch (so a clean sweep means something)
# --------------------------------------------------------------------------- #
def test_each_rule_fires_on_the_shape_it_names():
    assert sweep.internal_id_findings("ranked by magic_formula_raw_v1", "x")
    assert sweep.internal_id_findings("list adhoc:3f9a1c2b ran", "x")
    assert sweep.internal_id_findings("the distribution_yield factor", "x")
    assert not sweep.internal_id_findings("Magic Formula RAW ranked it first", "x")
    assert sweep.money_minus_findings("free cash flow FY2025 $-147.8bn", "x")
    assert sweep.money_minus_findings("€-3.2bn", "x")
    assert not sweep.money_minus_findings("-$147.8bn", "x")
    assert sweep.count_grammar_findings("1 name were ranked", "x")
    assert sweep.count_grammar_findings("1 of these names are ETFs", "x")
    assert sweep.count_grammar_findings("0 name(s) were given a position", "x")
    assert sweep.count_grammar_findings("3 companie(ies)", "x") is not None
    assert not sweep.count_grammar_findings("1 name was ranked; 2 names were excluded", "x")
    assert sweep.markup_findings("**bold only on one side", "x", markdown=True)
    assert sweep.markup_findings("a stray _word", "x", markdown=True)
    assert not sweep.markup_findings("**bold** and _italic_ and snake_case", "x", markdown=True)
    assert sweep.markup_findings("**leaked** into html", "x", markdown=False)
    assert sweep.internal_id_findings("would-rank not available: roic - abstained; "
                                      "revenue growth - abstained", "x")
    assert sweep.internal_id_findings("Grading with magic_formula_momentum_v1 (1 of 9)", "x")
    assert not sweep.internal_id_findings("Grading with Value + Momentum (1 of 9)", "x")
    assert sweep.strategy_word_findings("Strategy: Magic Formula RAW", "x")
    assert sweep.strategy_word_findings("not in this strategy's scope", "x")
    assert not sweep.strategy_word_findings("Lens: Magic Formula RAW", "x")
    from aristos_council.glossary import DETAIL_BADGE_NOTE
    assert sweep.badge_paragraph_findings("**Growth - 3 names. " + DETAIL_BADGE_NOTE + "**", "x")
    assert not sweep.badge_paragraph_findings(
        "**Growth - 3 names.**\n⚠ = the share price is up more than +30% (see the glossary).\n"
        "What the terms mean\n" + DETAIL_BADGE_NOTE, "x")
    assert sweep.backwards_band_findings("band $10bn-$5bn", "x")
    assert sweep.backwards_band_findings("band $5bn-$5bn", "x")
    assert not sweep.backwards_band_findings("band $3bn-$5bn", "x")


def test_the_structured_rules_fire_on_a_contradiction():
    from aristos_council.company_report import LensVote, build_agreement
    ranked = [SimpleNamespace(verdict=v, excluded=False, combined_rank=i)
              for i, v in enumerate(("buy", "hold", "hold", "hold", "hold"))]       # 5 names: a lens needs 5 (B25-1)
    assert sweep.buy_without_sell_findings("lens", ranked)
    ranked[-1] = SimpleNamespace(verdict="sell", excluded=False, combined_rank=4)
    assert not sweep.buy_without_sell_findings("lens", ranked)
    tied = [SimpleNamespace(verdict=v, excluded=False, combined_rank=r)            # B25-3: a tied bottom is no SELL
            for v, r in (("buy", 1), ("hold", 2), ("hold", 3), ("hold", 4), ("hold", 4))]
    assert not sweep.buy_without_sell_findings("lens", tied)
    ranked2 = [SimpleNamespace(verdict="buy", excluded=False)] * 2           # under 5: no cut
    assert not sweep.buy_without_sell_findings("lens", ranked2)

    votes = [LensVote("a", "A", status="ranked", verdict="sell", position=3, cohort_size=3)]
    report = SimpleNamespace(agreement=build_agreement(votes), votes=votes, peer_group=None)
    assert not sweep.company_agreement_findings(report, "x")                 # headline says SELL
    report.agreement = SimpleNamespace(sell=("A",), headline="BUY on 0 of 1 vote", checks={})
    assert sweep.company_agreement_findings(report, "x")                     # the old line
    forensic = LensVote("f", "Forensic", kind="check", status="excluded", reason="r")
    report2 = SimpleNamespace(agreement=SimpleNamespace(sell=(), headline="h",
                                                        checks={"Forensic": "clean"}),
                              votes=[forensic], peer_group=None)
    assert any(f.rule == sweep.LENS_TWO_WAYS
               for f in sweep.company_agreement_findings(report2, "x"))


# --------------------------------------------------------------------------- #
# ABSOLUTE-UI-1 - the company page's header and context are written as context
# --------------------------------------------------------------------------- #
def test_the_company_page_header_and_context_use_the_new_wording(sweep_reports):
    """In the text, the HTML and the Markdown: the new kicker and the context row are in the header, the old kicker
    is nowhere, and the header, the answer, the story, the gate, the price test, the return range and the context never
    say votes, cohort, strategy, "a BUY needs" or print a lens id."""
    from aristos_council.company_report import HOUSE_LINE, KICKER
    found, kinds = [], set()
    for where, kind, text, report in _company_exports(sweep_reports):
        kinds.add(kind)
        low = " ".join(text.lower().split())
        assert KICKER.lower() in low, where
        assert " ".join(HOUSE_LINE.lower().split()) in low, where
        assert "ranked among rivals on" in low or "not ranked among rivals" in low, where
        found += sweep.page_context_wording_findings(text, where)
    assert kinds == {"text", "html", "company md"}
    _assert_clean(found, sweep.PAGE_CONTEXT_WORDING)


def test_the_company_page_context_names_what_each_lens_measures(sweep_reports):
    """Every relative lens that ran shows up under "Where it sits among rivals" in the form "where it sits among
    rivals on <what it measures>" (a lens that lists factors always names them)."""
    for where, kind, text, report in _company_exports(sweep_reports):
        assert report.votes, where
        for v in report.votes:
            assert v.measures, (where, v.label)
            assert f"{v.label}: where it sits among rivals on {v.measures}" in " ".join(text.split()), (where, v.label)


def test_the_context_wording_rule_fires_on_the_shapes_it_names():
    ctx = "\nCONTEXT, NO VOTE\nWhere it sits among rivals:\nValue: where it sits among rivals on earnings yield: 4th of 13.\n"
    fold = "SHOW THE WORKINGS\nRelative view: the lenses cast votes here.\n"
    assert not sweep.page_context_wording_findings("Company Report - X\nThe answer\nfine." + ctx + fold, "x")
    for bad in ("the lenses cast votes", "a BUY needs a top-half rank", "the cohort of 13", "the strategy", "ranked by magic_formula_raw_v1"):
        assert sweep.page_context_wording_findings(f"Company Report - X\nThe answer\n{bad}\n" + ctx + fold, "x"), bad
        assert sweep.page_context_wording_findings("The answer\nfine." + ctx.replace("Value:", bad + " Value:") + fold, "x"), bad
    assert sweep.page_context_wording_findings("ONE COMPANY AGAINST ITS PEER GROUP\nThe answer\nfine." + ctx + fold, "x")
    assert sweep.page_context_wording_findings("The answer\nfine." + ctx + fold + "one company against its peer group", "x")
