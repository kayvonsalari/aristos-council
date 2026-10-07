"""B22-B11 (FINANCE-ARM-1) - a muted note for companies whose debt is mostly a captive finance arm's.
Ford: "owes $141.2bn net of cash ... 11.3 years of free cash flow to repay its debt" includes Ford Credit.
Note only: no number changes."""
from pathlib import Path

import yaml

from aristos_council.abs_readings import debt_and_cash
from aristos_council.finance_arms import finance_arm_note
from tests.test_abs_readings import _f

NOTE = "Includes debt of its car-loan arm, which is backed by customer loans, so this overstates the risk."
DATA = Path(__file__).resolve().parents[1] / "data" / "finance_arms.yaml"


def test_the_named_automakers_get_the_note_and_others_do_not():
    for t in ("F", "GM", "TM", "HMC", "STLA", "f", "7203.T", "VOW3.DE"):
        assert finance_arm_note(t) == NOTE, t
    for t in ("AAPL", "MSFT", "JPM", "", None, "FORD"):
        assert finance_arm_note(t) == "", t


def test_the_data_file_is_dated_and_reasoned_for_every_company():
    doc = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    assert doc["note"].strip().split() == NOTE.split()
    assert len(doc["companies"]) >= 5
    for c in doc["companies"]:
        for key in ("name", "arm", "tickers", "date", "reason"):
            assert c.get(key), (c.get("name"), key)


def test_the_note_changes_no_number():
    plain = debt_and_cash(_f(ticker="AAPL", total_debt=141.2e9 + 5e9, total_cash=5e9, free_cash_flow=12.5e9))
    ford = debt_and_cash(_f(ticker="F", total_debt=141.2e9 + 5e9, total_cash=5e9, free_cash_flow=12.5e9))
    assert ford.lines() == plain.lines()
    assert ford.notes() == [NOTE] and plain.notes() == []


def test_a_bank_listed_nowhere_gets_no_note_and_the_note_sits_in_the_story_and_every_export(tmp_path):
    from aristos_council.company_check import absolute_reading_lines
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import company_facts_pack, format_company_report
    from aristos_council.company_story import story_paragraphs
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run
    report = _run([RAW], tmp_path=tmp_path)
    report.check.debt_and_cash = debt_and_cash(_f(ticker="F", total_debt=146.2e9, total_cash=5e9,
                                                  free_cash_flow=12.5e9))
    assert NOTE in dict(story_paragraphs(report))["Other facts."]
    assert any(NOTE in ln for ln in absolute_reading_lines(report.check))
    assert NOTE in format_company_report(report) and NOTE in company_report_markdown(report)
    assert NOTE in company_report_html(report)
    assert NOTE in company_facts_pack(report)["absolute_readings"]["debt_and_cash"]
    report.check.debt_and_cash = debt_and_cash(_f(ticker="JPM", sector="Financial Services"))
    assert NOTE not in format_company_report(report)
