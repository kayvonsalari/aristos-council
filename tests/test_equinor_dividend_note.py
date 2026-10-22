"""EQUINOR-CHECK-1 (a) - the cut verdict stands; the page says why the fall looks like a cut.

Live, 2026-10-22 sweep, EQNR.OL on Cyclical Income: "the dividend was cut in 2025: the year's total fell 40%
and the typical payment fell 50% against 2024". Equinor's extraordinary dividend was paid inside the same
quarterly payments and ended for 2025; the payment history cannot separate the two. Owner ruling: keep the
verdict, add one plain line.
"""
from __future__ import annotations

from types import SimpleNamespace

from aristos_council.company_report import LensVote
from aristos_council.company_story import table_rows
from aristos_council.dividend_notes import extraordinary_dividend_note

LINE = ("The fall reflects the end of an extraordinary dividend paid inside the regular payments; the data "
        "cannot separate the two.")
CUT = ("the dividend was cut in 2025: the year's total fell 40% and the typical payment fell 50% against "
       "2024; the rule requires no year paid less than the year before, across the last 5 complete years")


def _report(ticker, reason=CUT):
    vote = LensVote("cyclical_income_v1", "Cyclical Income", status="excluded", reason=reason)
    return SimpleNamespace(ticker=ticker, votes=[vote])


def test_equinor_gets_the_line_beside_its_cut_reason_and_stays_excluded():
    row = table_rows(_report("EQNR.OL"))[0]
    assert row.full_reason.endswith(LINE)
    assert "the dividend was cut in 2025" in row.full_reason.lower()
    assert row.outcome == "does not apply"                     # the verdict stands


def test_other_companies_and_other_reasons_get_nothing():
    assert table_rows(_report("SHEL.L"))[0].full_reason == ""
    assert extraordinary_dividend_note("EQNR.OL", "dividend yield 0.5%; the rule requires at least 1.5%") == ""
    assert extraordinary_dividend_note("EQNR", CUT) == LINE
