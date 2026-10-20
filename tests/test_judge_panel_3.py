"""JUDGE-PANEL-3 - the full-evidence pack, labelled own knowledge, the page cards, and scoring.

Every model call is a fake, the statements and news are fixtures, and prices are an offline
fixture: nothing here reaches a provider.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date

import pytest

from aristos_council.company_report import format_company_report, report_record
from aristos_council.judges import answer as ans
from aristos_council.judges import log as jlog
from aristos_council.judges import panel, render, score
from aristos_council.judges.blind_pack import NEWS_SECTION, build_blind_pack, find_leaks
from tests.test_judge_panel import (KEYS, RAW, STRAT_DIR, _config, _dated_fundamentals, _record, _reply,
                                    _run, _run_panel, _scripted, report)  # noqa: F401  (report is a fixture)

pytestmark = pytest.mark.min_group(3)

STATEMENTS = {
    "income": {"periods": ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"],
               "rows": {"Total Revenue": [43.0e9, 45.8e9, 47.1e9, 47.9e9],
                        "Net Income": [9.5e9, 10.7e9, 10.6e9, 13.1e9],
                        "Diluted EPS": [2.19, 2.47, 2.46, 3.04],
                        "Selling General And Administration": [14.0e9, 14.8e9, 15.1e9, 15.4e9]}},
    "balance": {"periods": ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"],
                "rows": {"Total Debt": [40.0e9, 42.0e9, 44.0e9, 45.0e9],
                         "Ordinary Shares Number": [4.32e9, 4.31e9, 4.30e9, 4.30e9]}},
    "cashflow": {"periods": ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"],
                 "rows": {"Free Cash Flow": [9.5e9, 9.7e9, 4.7e9, 5.3e9],
                          "Income Tax Paid Supplemental Data": [3.0e9, 3.1e9, 9.0e9, 3.2e9],
                          "Change In Income Tax Payable": [0.1e9, 0.1e9, -5.9e9, 0.2e9],
                          "Net Business Purchase And Sale": [-0.2e9, -0.1e9, -1.0e9, -0.3e9]}},
}
NEWS = [
    {"date": "2026-10-17", "title": "Coca-Cola Tops Buy Point After Landing A Whale", "source": "x.com",
     "snippet": "The beverage maker cleared a buy zone, analysts say it should hold gains."},
    {"date": "2026-10-16", "title": "Coca-Cola to supply KFC from 2027", "source": "y.com", "snippet": ""},
] + [{"date": "2026-10-%02d" % d, "title": f"Headline number {d}", "source": "z.com", "snippet": "s"}
     for d in range(1, 12)]


def _pack(report, **kw):
    return build_blind_pack(report, _dated_fundamentals(), STATEMENTS, NEWS, strategies_dir=STRAT_DIR, **kw)


# =========================================================================== #
# 1. the pack: everything except Aristos' own opinions
# =========================================================================== #
def test_the_pack_carries_the_full_statements_including_the_cash_flow_lines_that_explain_a_swing(report):
    text = _pack(report).text()
    for needle in ("INCOME STATEMENT", "BALANCE SHEET", "CASH FLOW STATEMENT",
                   "fiscal years ending 2022-12-31, 2023-12-31, 2024-12-31, 2025-12-31",
                   "Income Tax Paid Supplemental Data", "Change In Income Tax Payable",
                   "Net Business Purchase And Sale", "Free Cash Flow", "Total Debt"):
        assert needle in text, needle
    # every statement line the provider gave is there, oldest first
    assert "Total Revenue: $43.0bn | $45.8bn | $47.1bn | $47.9bn" in text
    assert "Change In Income Tax Payable: $100.0m | $100.0m | -$5.9bn | $200.0m" in text


def test_statement_cells_are_formatted_by_what_they_are():
    text = _pack_text_only()
    assert "Diluted EPS: 2.19 | 2.47 | 2.46 | 3.04" in text                  # per share keeps its decimals
    assert "Ordinary Shares Number: 4.32bn | 4.31bn | 4.30bn | 4.30bn" in text   # a count has no currency


def _pack_text_only():
    from aristos_council.judges.blind_pack import _statement
    return "\n".join(_statement(STATEMENTS, "income", "USD") + _statement(STATEMENTS, "balance", "USD"))


def test_the_pack_keeps_every_headline_with_its_date_and_snippet_not_just_five(report):
    pack = _pack(report)
    news = dict(pack.sections)[NEWS_SECTION]
    assert len(news) == len(NEWS) == 13
    assert news[0].startswith("2026-10-17 [x.com] Coca-Cola Tops Buy Point After Landing A Whale - The beverage maker")
    assert any("KFC" in n for n in news)


def test_a_headline_may_say_buy_because_it_is_the_worlds_word_not_aristos(report):
    pack = _pack(report)
    assert "Buy Point" in pack.text() and not pack.dropped


def test_a_headline_naming_a_lens_is_still_dropped(report):
    lens = report.votes[0].label
    news = NEWS + [{"date": "2026-10-18", "title": f"{lens} rates it", "source": "q", "snippet": ""}]
    pack = build_blind_pack(report, None, None, news, strategies_dir=STRAT_DIR)
    assert any(title == NEWS_SECTION for title, _, _ in pack.dropped)
    assert f"{lens} rates it" not in pack.text()


def test_everything_but_the_news_is_free_of_lens_names_verdict_words_and_badge_words(report):
    pack = _pack(report)
    assert pack.dropped == []
    body = "\n".join(f"{t.upper()}\n" + "\n".join(lines) for t, lines in pack.sections if t != NEWS_SECTION)
    assert find_leaks(body, strategies_dir=STRAT_DIR, check_generic_names=False) == []
    assert not re.search(r"\b(?:buy|hold|sell|clean|doubted)\b", body, re.I)
    for v in report.votes:
        assert v.strategy_id not in pack.text() and v.label not in pack.text()


def test_a_statement_line_that_leaked_a_verdict_word_would_be_dropped(report):
    bad = {**STATEMENTS, "income": {"periods": ["2025-12-31"], "rows": {"Analysts BUY Rating": [1.0],
                                                                          "Total Revenue": [2.0]}}}
    pack = build_blind_pack(report, None, bad, None, strategies_dir=STRAT_DIR)
    assert any("Analysts BUY Rating" in line for _, line, _ in pack.dropped)
    assert "Analysts BUY Rating" not in pack.text() and "Total Revenue" in pack.text()


def test_peers_carry_name_market_cap_and_sub_industry_and_no_rank(report):
    peers = dict(_pack(report).sections)["Peers"]
    assert len(peers) >= 10
    assert all(" - market cap " in p and "Semiconductors" in p for p in peers)
    assert not any(re.search(r"rank|\d+(st|nd|rd|th) of", p, re.I) for p in peers)


def test_forensic_raw_numbers_are_present_without_the_label_or_ranks(report):
    section = dict(_pack(report).sections)["Accounting-quality readings (raw values)"]
    assert not any(re.search(r"rank\s+\d+\s+of|clean|doubted|no concern", ln, re.I) for ln in section)


# =========================================================================== #
# 2. own knowledge, labelled
# =========================================================================== #
def _full(**over):
    doc = {"verdict": "HOLD", "confidence": "medium",
           "driving_fact": "Free cash flow halved to $5.3bn in FY2025 [pack].",
           "reasoning": "Free cash flow fell in FY2024 [pack]. The company paid a one-off tax deposit that year [own knowledge].",
           "key_risk": "Valuation is at the top of its range.", "unexplained": "nothing",
           "would_change_my_mind": "Free cash flow back above $9bn."}
    doc.update(over)
    return json.dumps(doc)


def test_a_fully_labelled_answer_parses_and_counts_its_own_knowledge_claims():
    got, why = ans.parse_answer(_full())
    assert why == "" and got.verdict == "HOLD" and got.own_knowledge_claims == 1
    assert ans.segments(got.reasoning) == [("Free cash flow fell in FY2024.", "pack"),
                                           ("The company paid a one-off tax deposit that year.", "own knowledge")]


def test_tags_are_normalised_to_one_spelling():
    got, _ = ans.parse_answer(_full(driving_fact="Cash was $5bn [PACK].",
                                    reasoning="It owes $9bn [Own Knowledge]."))
    assert got.driving_fact.endswith("[pack].") and got.reasoning.endswith("[own knowledge].")


@pytest.mark.parametrize("over, fragment", [
    ({"reasoning": "Free cash flow fell. The company paid a deposit."}, "no claim is labelled"),
    ({"driving_fact": "Free cash flow halved."}, "driving_fact: no claim is labelled"),
    ({"reasoning": "Cash fell [pack]. It was a tax deposit."}, "unlabelled"),
    ({"reasoning": "Cash fell. Debt rose. Both are in the pack [pack]."}, "more than one sentence"),
    ({"reasoning": "Cash fell [filing]."}, "unknown source label"),
    ({"reasoning": "[pack] Cash fell [pack]."}, "no claim before it"),
    ({"reasoning": "Cash fell [pack][own knowledge]."}, "no claim before it"),
    ({"reasoning": " ".join(["word"] * 151) + " [pack]."}, "longer than 150 words"),
])
def test_an_unlabelled_or_malformed_claim_is_a_format_error(over, fragment):
    got, why = ans.parse_answer(_full(**over))
    assert got is None and fragment in why


def test_the_old_four_key_answer_is_no_longer_valid():
    old = json.dumps({"verdict": "BUY", "confidence": "low", "reason": "r [pack]", "would_change_my_mind": "w"})
    got, why = ans.parse_answer(old)
    assert got is None and "missing key" in why


def test_an_unlabelled_answer_is_retried_once_then_recorded_as_invalid_output():
    spec = _config().judges[0]
    caller = _scripted({"anthropic": [_full(reasoning="No labels at all."), _full()]})
    call = panel._ask_once(spec, "s", "u", caller, 1)
    assert call.status == "ok" and call.attempts == 2 and call.own_knowledge_claims == 1
    assert "[own knowledge]" in caller.calls[1][1]                       # the retry names the labels
    always = _scripted({"anthropic": [_full(reasoning="No labels at all.")]})
    call = panel._ask_once(spec, "s", "u", always, 1)
    assert call.status == "invalid" and call.error == "invalid output" and "reasoning" in call.error_detail


def test_the_prompt_allows_own_knowledge_but_demands_labels_and_the_new_fields():
    from aristos_council.judges.config import prompt_text
    text = prompt_text()
    for needle in ("[pack]", "[own knowledge]", "driving_fact", "reasoning", "key_risk", "unexplained",
                   "would_change_my_mind", "150-word", "ABSTAIN", "cash flow"):
        assert needle in text, needle


# =========================================================================== #
# 3. the page: cards, marking, position, the agreement line
# =========================================================================== #
def _judge(label, family, verdict, reasoning, status=panel.STABLE, unexplained="nothing"):
    j = panel.JudgeResult(family, label, "m-" + family, status=status)
    call = lambda n: panel.JudgeCall(n, "ok", verdict, "medium", reasoning, would_change_my_mind="w", driving_fact="A fact [pack].",
                                     key_risk="A risk.", unexplained=unexplained, own_knowledge_claims=1)
    j.calls = [call(1), call(2)]
    return j


def _mixed_panel():
    mixed = "Cash fell [pack]. A one-off tax deposit explains it [own knowledge]."
    res = panel.PanelResult(ticker="CO", available=True, record=_record("HOLD", "BUY 0 / HOLD 2 / SELL 0"),
                            judges=[_judge("Claude", "anthropic", "HOLD", mixed),
                                    _judge("OpenAI", "openai", "HOLD", mixed),
                                    _judge("Gemini", "google", "HOLD", mixed)])
    res.agreement = panel.panel_agreement(res.judges, res.record)
    return res


def test_own_knowledge_passages_are_marked_italic_with_a_tag_and_pack_passages_are_not():
    md = render.mark_markdown("Cash fell [pack]. A one-off tax deposit explains it [own knowledge].")
    assert md == "Cash fell. *A one-off tax deposit explains it.* `own knowledge`"
    html = render.mark_html("Cash fell [pack]. A one-off tax deposit explains it [own knowledge].")
    assert '<em>A one-off tax deposit explains it.</em> <span class="tag own-knowledge">own knowledge</span>' in html
    assert "<em>Cash fell" not in html


def test_the_agreement_line_names_the_record_with_lens_votes_never_judge_counts():
    (line,) = render.summary_lines(_mixed_panel())
    assert line == "Verdict of record HOLD (lens votes: 0 BUY, 2 HOLD, 0 SELL). Panel: 3 HOLD — agrees."


def test_the_agreement_line_in_the_other_cases():
    res = _mixed_panel()
    res.judges[0] = _judge("Claude", "anthropic", "SELL", "x [pack].")
    res.agreement = panel.panel_agreement(res.judges, res.record)
    assert render.summary_lines(res)[0].endswith("Panel: 1 SELL, 2 HOLD — mixed against the record.") or \
        "Panel: 2 HOLD, 1 SELL" in render.summary_lines(res)[0]
    res.judges = res.judges[:1] + [panel.JudgeResult("openai", "OpenAI", "m", status=panel.ERROR, note="429")]
    res.agreement = panel.panel_agreement(res.judges, res.record)
    assert "No panel reading: fewer than two judges answered." in render.summary_lines(res)[0]


def test_the_page_shows_one_card_per_judge_with_why_folded_and_own_knowledge_marked():
    from streamlit.testing.v1 import AppTest

    def script():
        import app
        from tests.test_judge_panel_3 import _mixed_panel
        app._render_judge_panel(_mixed_panel())

    at = AppTest.from_function(script, default_timeout=60).run()
    assert not at.exception
    md = "\n".join(str(m.value) for m in at.markdown)
    assert [e.label for e in at.expander] == ["Why", "Why", "Why"]                # reasoning folded, per judge
    assert md.count("**Claude (m-anthropic)** — **HOLD** · confidence medium") == 1
    assert "The fact that drove it:* A fact" in md and "Key risk:* A risk." in md
    assert "Could not account for:* nothing" in md and "Would change its mind if:* w" in md
    why = "\n".join(str(m.value) for e in at.expander for m in e.markdown)
    assert "*A one-off tax deposit explains it.* `own knowledge`" in why
    assert "Verdict of record HOLD (lens votes: 0 BUY, 2 HOLD, 0 SELL). Panel: 3 HOLD — agrees." in md
    assert any("no vote" in c.value or "have no" in c.value for c in at.caption)


def test_an_error_card_shows_only_its_short_label():
    res = _mixed_panel()
    res.judges[2] = panel.settle(_config().judges[2], [panel.JudgeCall(1, "error", error="no credits",
                                                                       error_detail="raw")] * 2)
    card = render.cards(res)[2]
    assert card["verdict"] == "error: no credits" and not card["driving_fact"] and not card["reasoning"]


def test_the_section_sits_between_the_lens_table_and_the_council_opinion_and_the_workings(report):
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import CouncilOpinion
    from aristos_council.export.report_html import company_report_html

    report.judge_panel = _mixed_panel()
    report.council_opinion = CouncilOpinion(available=True, narrative="Council words.")
    md, txt, html = company_report_markdown(report), format_company_report(report), company_report_html(report)
    assert md.index("## Judge panel") < md.index("## Council opinion") < md.index("## Show the workings")
    assert txt.index("JUDGE PANEL") < txt.index("COUNCIL OPINION") < txt.index("SHOW THE WORKINGS")
    assert html.index("Judge panel") < html.index("Council opinion") < html.index("Show the workings")
    assert "own knowledge" in md and "own knowledge" in html and "[own knowledge]" in txt


def test_the_app_places_the_panel_right_under_the_lens_table():
    src = open("app.py", encoding="utf-8").read()
    body = src[src.index("def _render_company_report"):src.index("def _render_judge_panel")]
    assert body.index("company_cards.lens_table(report)") < body.index("_render_judge_panel(report.judge_panel)") \
        < body.index("company_cards.stat_cards(report)") < body.index("Council opinion") \
        < body.index("SECTION_WORKINGS)")


# ---- "what to doubt" ---------------------------------------------------------------------------
def test_the_story_lists_what_the_judges_could_not_account_for_only_when_the_panel_ran(report):
    from aristos_council.company_story import story_page

    def doubt(r):
        return next(t for lead, t in story_page(r).paragraphs if "doubt" in lead.lower())

    before = doubt(report)
    assert "could not account for" not in before
    res = _mixed_panel()
    res.judges[0] = _judge("Claude", "anthropic", "HOLD", "x [pack].",
                           unexplained="Why free cash flow halved in FY2024 [pack]")
    res.judges[1] = _judge("OpenAI", "openai", "HOLD", "x [pack].", unexplained="nothing")
    report.judge_panel = res
    after = doubt(report)
    assert after.endswith("The judge panel could not account for: Claude: Why free cash flow halved in "
                          "FY2024.")                                     # STORY-TAG-LEAK-1: no [pack] in prose
    assert after.startswith(before) or before in after
    report.judge_panel = panel.PanelResult(available=False, note="n")
    assert doubt(report) == before                                       # a panel that did not run adds nothing


# =========================================================================== #
# the contract, again, with the new fields
# =========================================================================== #
def test_lens_output_stays_identical_with_the_full_evidence_panel_on_or_off(tmp_path):
    caller = _scripted({"anthropic": [_full(unexplained="Why cash fell [pack]")],
                        "openai": [_full(verdict="SELL")], "google": [_full(verdict="BUY")]})
    off = _run([RAW, "forensic_v1"], tmp_path=tmp_path / "a", save=False)
    on = _run([RAW, "forensic_v1"], tmp_path=tmp_path / "b", save=False, with_judges=True,
              judge_caller=caller, judge_config=_config(), judges_dir=tmp_path / "j", judge_environ=KEYS,
              judge_statements=lambda t: (STATEMENTS, ""), judge_news=lambda t: (NEWS, ""),
              judge_reader=lambda r, rep, pack: panel.PanelReading(paragraph="Reading.", calls=1))
    assert on.agreement == off.agreement and on.lens_ranks == off.lens_ranks
    assert [(v.strategy_id, v.verdict, v.position, v.badge) for v in on.votes] == \
        [(v.strategy_id, v.verdict, v.position, v.badge) for v in off.votes]
    a, b = report_record(on), report_record(off)
    for rec in (a, b):
        for k in ("judge_panel", "seconds", "run_at", "cache"):
            rec.pop(k, None)
    assert a == b
    # the pack the judges were handed carried the statements and the news
    user = caller.calls[0][1]
    assert "Income Tax Paid Supplemental Data" in user and "Headline number 5" in user
    # and the only prose difference is the panel section plus the one story sentence
    for r in (on, off):
        r.seconds, r.cache, r.saved_to = 0.0, {}, ""
    t_on = re.sub(r"JUDGE PANEL.*?(?=\nSHOW THE WORKINGS)", "", format_company_report(on), flags=re.S)
    t_on = re.sub(r" The judge panel could not account for: [^\n]*", "", t_on).replace("\n\n\n", "\n\n")
    assert t_on == format_company_report(off)


# =========================================================================== #
# 4. scoring
# =========================================================================== #
def _series(start: date, prices: dict[int, float], days=500):
    """A daily series from ``start``: ``prices`` maps day-offset to price, linear in between."""
    from datetime import timedelta
    keys = sorted(prices)
    out = []
    for i in range(days):
        lo = max(k for k in keys if k <= i) if any(k <= i for k in keys) else keys[0]
        hi = min((k for k in keys if k >= i), default=keys[-1])
        px = prices[lo] if hi == lo else prices[lo] + (prices[hi] - prices[lo]) * (i - lo) / (hi - lo)
        out.append((start + timedelta(days=i), px))
    return out


D0 = date(2025, 1, 2)


def _fixture_prices():
    """CO: +20% over the first 3 months, flat after. Five peers: +10%, then flat. So the excess is
    +10 points at 3 months, and +10 at 6 and 12 months."""
    return {
        "CO": _series(D0, {0: 100.0, 90: 120.0, 400: 120.0}),
        "FLAT": _series(D0, {0: 50.0, 400: 51.0}),                   # +2% over a year
        **{f"P{i}": _series(D0, {0: 100.0, 90: 110.0, 400: 110.0}) for i in range(5)},
    }


def _row(**kw):
    base = {"run_date": "2025-01-02T10:00:00+00:00", "ticker": "CO", "verdict_of_record": "BUY",
            "claude_verdict": "BUY", "openai_verdict": "HOLD", "gemini_verdict": "ABSTAIN",
            "peers": ";".join(f"P{i}" for i in range(5))}
    base.update(kw)
    return base


def _scored(rows, today):
    return score.score_rows(rows, _fixture_prices(), today)


def _status(scored, subject, months):
    return next(r["status"] for r in scored if r["subject"] == subject and int(r["window_months"]) == months)


def test_the_win_band_is_two_points_each_way_and_exactly_two_is_not_a_win_for_buy_or_sell():
    assert score.outcome("BUY", 2.01) == "right" and score.outcome("BUY", 2.0) == "wrong"
    assert score.outcome("SELL", -2.01) == "right" and score.outcome("SELL", -2.0) == "wrong"
    assert score.outcome("HOLD", 2.0) == "right" and score.outcome("HOLD", -2.0) == "right"
    assert score.outcome("HOLD", 2.01) == "wrong" and score.outcome("BUY", -5) == "wrong"
    assert score.outcome("ABSTAIN", 50) == "not scored"


def test_months_are_added_by_calendar_and_clamped_to_the_month_end():
    assert score.add_months(date(2025, 1, 2), 3) == date(2025, 4, 2)
    assert score.add_months(date(2025, 11, 30), 3) == date(2026, 2, 28)
    assert score.add_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
    assert score.add_months(date(2025, 3, 15), 12) == date(2026, 3, 15)


def test_each_verdict_is_scored_against_the_cohort_over_known_outcomes():
    s = _scored([_row()], today=date(2026, 2, 1))                     # 13 months on: every window has ended
    for months in (3, 6, 12):
        assert _status(s, "lenses", months) == "right"                # BUY, beat the cohort by 10 points
        assert _status(s, "claude", months) == "right"
        assert _status(s, "openai", months) == "wrong"                # HOLD, but it was 10 points off
        assert _status(s, "gemini", months) == "not scored"           # ABSTAIN is never scored
    first = next(r for r in s if r["subject"] == "claude" and r["window_months"] == 3)
    assert float(first["excess_pp"]) == pytest.approx(10.0, abs=0.01) and first["n_peers"] == 5
    assert float(first["company_return"]) == pytest.approx(0.20, abs=1e-6)
    assert float(first["benchmark_return"]) == pytest.approx(0.10, abs=1e-6)


def test_sell_is_right_when_the_name_trails_and_wrong_when_it_leads():
    s = _scored([_row(verdict_of_record="SELL", claude_verdict="SELL")], today=date(2026, 2, 1))
    assert _status(s, "lenses", 3) == "wrong"                          # it led by 10, so SELL was wrong
    low = dict(_fixture_prices(), CO=_series(D0, {0: 100.0, 90: 80.0, 400: 80.0}))
    s = score.score_rows([_row(verdict_of_record="SELL")], low, date(2026, 2, 1))
    assert _status(s, "lenses", 3) == "right"                          # trailed by 30 points


def test_a_window_that_has_not_ended_is_too_early_and_every_verdict_is_too_early_at_a_week():
    s = _scored([_row()], today=date(2025, 1, 9))
    assert {r["status"] for r in s if r["subject"] in ("lenses", "claude", "openai")} == {"too early"}
    s = _scored([_row()], today=date(2025, 5, 1))                      # past 3 months only
    assert _status(s, "lenses", 3) == "right" and _status(s, "lenses", 6) == "too early" \
        and _status(s, "lenses", 12) == "too early"


def test_the_return_since_is_reported_even_while_every_window_is_too_early():
    s = _scored([_row()], today=date(2025, 2, 15))
    r = next(x for x in s if x["subject"] == "claude")
    assert r["status"] == "too early" and r["since_return"] != "" and r["since_excess_pp"] != ""


def test_unstable_errored_and_mixed_verdicts_are_not_scored():
    row = _row(verdict_of_record="MIXED", claude_verdict="UNSTABLE:BUY/HOLD", openai_verdict="ERROR",
               gemini_verdict="NOT_RUN")
    s = _scored([row], today=date(2026, 2, 1))
    assert {r["status"] for r in s} == {"not scored"}


def test_too_few_peers_with_prices_means_no_benchmark_not_a_guess():
    s = score.score_rows([_row(peers="P0;P1;GONE")], _fixture_prices(), date(2026, 2, 1))
    assert _status(s, "lenses", 3) == "no data"
    s = score.score_rows([_row(peers="")], _fixture_prices(), date(2026, 2, 1))
    assert _status(s, "lenses", 3) == "no data"


def test_the_price_on_the_judgement_date_is_the_first_close_on_or_after_it():
    series = [(date(2025, 1, 6), 10.0), (date(2025, 1, 7), 11.0)]       # 2 Jan was a holiday-ish gap
    assert score._price_at(series, date(2025, 1, 2), after=True) == 10.0
    assert score._price_at(series, date(2025, 1, 20), after=True) is None   # nothing within the slack
    assert score._price_at(series, date(2025, 1, 6), after=False) == 10.0


def test_disagreements_say_who_was_right_at_the_longest_scored_window():
    s = _scored([_row(verdict_of_record="BUY", claude_verdict="SELL", openai_verdict="HOLD")],
                today=date(2026, 2, 1))
    by = {d["subject"]: d for d in score.disagreements(s)}
    assert by["claude"]["right"] == "lenses" and by["claude"]["window"] == 12
    assert by["openai"]["right"] == "lenses"
    s = _scored([_row(verdict_of_record="HOLD", claude_verdict="BUY")], today=date(2026, 2, 1))
    assert score.disagreements(s)[0]["right"] == "judge"
    early = _scored([_row(claude_verdict="SELL")], today=date(2025, 1, 9))
    assert score.disagreements(early)[0]["right"] is None


def test_the_summary_counts_right_wrong_too_early_and_states_the_vote_bar():
    done = _scored([_row(), _row(ticker="CO")], today=date(2026, 2, 1))
    text = score.summary_text(done)
    assert "claude" in text and "lenses" in text and "12m" in text
    assert "No judge earns a vote until it has 12-month results across at least 30 names." in text
    assert "claude: 1 of 30 names with a 12-month result" in text and "gemini: 0 of 30" in text
    early = score.summary_text(_scored([_row()], today=date(2025, 1, 9)))
    assert "Every verdict is still too early to score" in early


def test_the_score_command_reads_the_log_writes_score_csv_and_prints_the_summary(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "judges"
    folder.mkdir()
    with (folder / jlog.SUMMARY_NAME).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=jlog.SUMMARY_COLUMNS)
        w.writeheader()
        w.writerow({**{c: "" for c in jlog.SUMMARY_COLUMNS}, **_row(), "pack_hash": "abc"})
    rows, text, path = score.run_score(folder, today=date(2026, 2, 1), fetcher=lambda t: _fixture_prices().get(t, []))
    assert path == folder / score.SCORE_NAME and path.exists()
    out = list(csv.DictReader(path.open(encoding="utf-8")))
    assert tuple(out[0].keys()) == score.SCORE_COLUMNS and len(out) == 12          # 4 subjects x 3 windows
    assert {r["status"] for r in out if r["subject"] == "lenses"} == {"right"}
    from aristos_council.judges import report as jreport
    # the CLI path with a fetcher-less run falls back to the production fetcher; keep it offline
    import aristos_council.judges.score as sc
    monkeypatch.setattr(sc, "production_fetcher", lambda *a, **k: (lambda t: _fixture_prices().get(t, [])))
    assert jreport.main(["score", "--dir", str(folder), "--today", "2025-01-09"]) == 0
    printed = capsys.readouterr().out
    assert "still too early" in printed and "Written to" in printed


def test_scoring_an_empty_log_says_so(tmp_path):
    rows, text, path = score.run_score(tmp_path / "none", today=date(2026, 2, 1), fetcher=lambda t: [])
    assert rows == [] and path is None and "nothing to score" in text


def test_the_summary_log_now_records_the_peers_the_scorer_needs(tmp_path, report):
    caller = _scripted({"anthropic": [_full()], "openai": [_full()], "google": [_full()]})
    result = _run_panel(report, caller)
    folder = jlog.write_log(result, tmp_path / "j")
    (row,) = list(csv.DictReader((folder / jlog.SUMMARY_NAME).open(encoding="utf-8")))
    assert row["peers"].startswith("P00;P01") and "CO" not in row["peers"].split(";")
    log = list(csv.DictReader((folder / jlog.LOG_NAME).open(encoding="utf-8")))
    assert {"driving_fact", "key_risk", "unexplained", "own_knowledge_claims"} <= set(log[0])
    assert log[0]["own_knowledge_claims"] == "1" and log[0]["driving_fact"].endswith("[pack].")


def test_scoring_changes_nothing_on_the_page():
    import ast
    import pathlib
    src = pathlib.Path("app.py").read_text(encoding="utf-8")
    assert "judges.score" not in src and "judges import score" not in src
    for mod in ("panel", "render", "narrate", "blind_pack", "log"):
        text = pathlib.Path(f"src/aristos_council/judges/{mod}.py").read_text(encoding="utf-8")
        assert "from .score" not in text and "import score" not in text
    ast.parse(src)
