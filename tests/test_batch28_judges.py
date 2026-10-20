"""Batch 28 A - JUDGE-PANEL-4: the claim counts, the footer, the story tag leak.

Every model call is a fake; nothing here reaches a provider."""
from __future__ import annotations

import pytest

from aristos_council.judges import answer, panel, render
from tests.test_judge_panel import _reply, _run_panel, _scripted, report  # noqa: F401  (report is a fixture)

pytestmark = pytest.mark.min_group(3)

MIXED = ("Free cash flow fell to $5.3bn. [pack] Interest cover is 9.0 times. [pack] "
         "Management has bought back stock for years. [own knowledge]")


def _three(reasons):
    caller = _scripted({f: [_reply("HOLD", reason=r, fact="Free cash flow was $5.3bn. [pack]")] * 2
                        for f, r in zip(("anthropic", "openai", "google"), reasons)})
    return _run_panel(report_obj, caller)


def test_count_claims_counts_each_tagged_sentence():
    assert answer.count_claims(MIXED) == (2, 1)
    assert answer.count_claims("A fact. [pack]", "Another. [own knowledge]") == (1, 1)
    assert answer.count_claims("") == (0, 0)


def test_each_judge_card_carries_its_claim_counts_and_the_line_reads_plainly(report):
    global report_obj
    report_obj = report
    result = _three([MIXED, "Cover is thin. [pack]", MIXED])
    cards = render.cards(result)
    assert [(c["claims_pack"], c["claims_own"]) for c in cards] == [(3, 1), (2, 0), (3, 1)]
    assert render.claims_line(cards[0]) == "3 claims from the pack, 1 from own knowledge"
    assert render.claims_line(cards[1]) == "2 claims from the pack, 0 from own knowledge"
    for lines in (render.text_lines(result), render.markdown_lines(result)):
        assert any("3 claims from the pack, 1 from own knowledge" in ln for ln in lines)


def test_the_footer_says_none_was_marked_only_when_no_judge_marked_any(report):
    global report_obj
    report_obj = report
    none = _three(["Cover is thin. [pack]"] * 3)
    assert render.own_tag_note(none) == "No passage was marked own knowledge in this run."
    assert render.text_lines(none)[-1] == "  (No passage was marked own knowledge in this run.)"
    some = _three([MIXED, "Cover is thin. [pack]", "Cover is thin. [pack]"])
    assert render.own_tag_note(some) == render.OWN_TAG_NOTE
    assert "No passage was marked" not in "\n".join(render.markdown_lines(some))


def test_a_panel_where_nobody_answered_keeps_the_standing_note(report):
    global report_obj
    report_obj = report
    caller = _scripted({f: [RuntimeError("x")] * 2 for f in ("anthropic", "openai", "google")})
    result = _run_panel(report, caller)
    assert render.own_tag_note(result) == render.OWN_TAG_NOTE


def test_the_story_sentence_carries_no_source_tags(report):
    global report_obj
    report_obj = report
    caller = _scripted({f: [_reply("HOLD", unexplained="The cause of the $21.3bn swing in other investing. [pack]")] * 2
                        for f in ("anthropic", "openai", "google")})
    line = render.unexplained_line(_run_panel(report, caller))
    assert line.startswith("The judge panel could not account for: ")
    assert "[pack]" not in line and "[own knowledge]" not in line
    assert line.endswith("$21.3bn swing in other investing.") or "swing in other investing." in line
    assert answer.strip_tags("It was $21.3bn. [own knowledge]") == "It was $21.3bn."


# --------------------------------------------------------------------------- #
# A3 PACK-NETDEBT-1
# --------------------------------------------------------------------------- #
def test_the_pack_dates_the_net_debt_reading_and_explains_the_statement_line(report):
    from aristos_council.data.adapter import Fundamentals
    from aristos_council.abs_readings import debt_and_cash
    from aristos_council.judges.blind_pack import build_blind_pack
    f = Fundamentals(ticker="CO", name="Co", financial_currency="USD", total_debt=200e9, total_cash=58.8e9,
                     operating_cash_flow=20e9, trailing_period_end="2026-06-30")
    report.check.debt_and_cash = debt_and_cash(f)
    pack = build_blind_pack(report, f)
    section = dict(pack.sections)["Debt and cash"]
    assert section[0].startswith("owes $141.2bn net of cash") and "balance sheet of Jun 2026" in section[0]
    assert any("year-end" in ln and "Net Debt" in ln for ln in section)
    # the net-debt figure appears once in the readings, with its date
    assert sum("net of cash" in ln for ln in section) == 1


# --------------------------------------------------------------------------- #
# A4 DIV-CUT-SPECIAL-1
# --------------------------------------------------------------------------- #
def _ford_years():
    import statistics
    per = {2021: [.15] * 4, 2022: [.15] * 4, 2023: [.8, .15, .15, .15], 2024: [.33, .15, .15, .15],
           2025: [.3, .15, .15, .15], 2026: [.15] * 3}
    stats = {y: (sum(a), statistics.median(a), len(a)) for y, a in per.items()}
    return per, stats, {y: sum(a) for y, a in per.items()}


def test_a_shrinking_special_is_not_a_cut_of_the_regular_dividend():
    from aristos_council.tools.screening import dividend_streak
    _per, stats, annual = _ford_years()
    assert dividend_streak(annual, 2026)[1] == 2025                       # totals alone: the old false flag
    assert dividend_streak(annual, 2026, year_stats=stats) == (0, None)   # regular $0.60 never moved


def test_a_real_cut_still_reads_as_a_cut_with_the_regular_dividend_rule():
    import statistics
    from aristos_council.tools.screening import dividend_streak
    per = {2018: [.5] * 4, 2019: [.5] * 4, 2020: [.5, .5, .1, .1], 2021: [.1] * 4, 2022: [.1] * 4, 2023: [.1] * 2}
    stats = {y: (sum(a), statistics.median(a), len(a)) for y, a in per.items()}
    annual = {y: sum(a) for y, a in per.items()}
    assert dividend_streak(annual, 2023, year_stats=stats)[1] == 2021


def test_cyclical_income_already_reads_the_regular_dividend_so_it_passes_the_ford_shape():
    from aristos_council.data.adapter import DividendEvent
    from aristos_council.tools.screening import max_dividend_cuts_criterion
    from datetime import date
    per, _stats, _annual = _ford_years()
    events = [DividendEvent(ex_date=date(y, 1 + 3 * i % 12, 10), amount=a)
              for y, amounts in per.items() for i, a in enumerate(amounts)]
    res = max_dividend_cuts_criterion(events, years=4)
    assert res.passed is True and res.observed == 0.0


def test_the_pack_labels_specials_apart_and_names_no_cut(report):
    from aristos_council.data.adapter import Fundamentals
    from aristos_council.judges.blind_pack import _dividends
    _per, stats, _annual = _ford_years()
    f = Fundamentals(ticker="F", name="Ford", dividend_per_share=0.6, last_dividend_reduction_year=None,
                     dividend_year_stats=[[float(y), t, m, float(n)] for y, (t, m, n) in stats.items()])
    lines = _dividends(f)
    assert not any("Most recent year" in ln for ln in lines)
    special = next(ln for ln in lines if "Supplemental or special" in ln)
    assert "2023 +0.65" in special and "2024 +0.18" in special and "inferred" in special
