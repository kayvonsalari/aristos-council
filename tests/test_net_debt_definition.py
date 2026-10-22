"""NET-DEBT-1 - one definition of net debt, on every surface.

Live, 2026-10-22 sweep:
* MSFT's page said "owes $52.0bn net of cash (balance sheet, Jun 2026)" while the judge pack's balance
  sheet carried the provider's own "Net Debt" line of $19.4bn for the same date. OpenAI: "an unexplained
  discrepancy".
* NVCR's judges saw "cash and investments $447.7m" and "net cash $207.1m" and no debt figure to
  reconcile them with.
* 1211.HK's page said "holds CNY 2.8bn more cash than debt (Jun 2026)" beside year-end total debt
  CNY 119.1bn and cash CNY 75.4bn.

The lines that feed the reading: the provider's latest-quarter ``totalDebt`` (borrowings INCLUDING
lease liabilities) and ``totalCash`` (cash, equivalents AND short-term investments). The provider's
statement "Net Debt" line leaves out leases and short-term investments; the year-end statement lines
are a different date. The definition is stated once (``NET_DEBT_DEFINITION``), the page shows its two
parts, the Quality factor and enterprise value use the same two fields, and the judge pack gives the
three figures as separate dated lines and drops the provider line.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import fields
from pathlib import Path

import pytest

from aristos_council.abs_readings import NET_DEBT_DEFINITION, debt_and_cash
from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import enterprise_value
from aristos_council.tools.quality_epv import net_debt_to_ebit
from tests.test_judge_panel import report  # noqa: F401  (fixture)

INPUTS = Path(__file__).resolve().parent / "fixtures" / "report_sweep" / "frozen" / "inputs"
_NAMES = {f.name for f in fields(Fundamentals)}


def _frozen(name: str) -> Fundamentals:
    raw = json.load(gzip.open(INPUTS / f"{name}.json.gz"))["fundamentals"]
    return Fundamentals(**{k: v for k, v in raw.items() if k in _NAMES})


def test_the_definition_is_stated_once_and_names_leases_and_short_term_investments():
    assert "lease" in NET_DEBT_DEFINITION and "short-term investments" in NET_DEBT_DEFINITION
    assert "latest balance sheet" in NET_DEBT_DEFINITION


def test_nvcr_the_page_gives_debt_cash_and_net_cash_that_add_up():
    f = _frozen("NVCR")                       # recorded 2026-10-05: debt $233.4m, cash $440.6m
    dc = debt_and_cash(f)
    lines = dc.lines()
    assert lines[:3] == ["holds $207.1m more cash than debt", "total debt $233.4m",
                         "cash and short-term investments $440.6m"]
    assert dc.cash.value - dc.total_debt.value == pytest.approx(-dc.net_debt.value)


def test_byd_the_reading_is_the_latest_quarter_not_the_year_end_statement():
    f = _frozen("1211_HK")
    dc = debt_and_cash(f)
    assert dc.total_debt.value == f.total_debt and dc.cash.value == f.total_cash
    assert dc.net_debt.label == "holds CNY 2.8bn more cash than debt"
    # the year-end statement lines (CNY 119.1bn debt, CNY 75.4bn cash and equivalents only) are a
    # different date and a narrower cash line; they are not what the reading uses
    assert f.aligned_annual["total_debt"][0] == pytest.approx(119.12064e9)
    assert dc.total_debt.value != f.aligned_annual["total_debt"][0]


def test_the_quality_factor_and_enterprise_value_use_the_same_two_figures():
    f = _frozen("F")
    dc = debt_and_cash(f)
    ratio, _ = net_debt_to_ebit(Fundamentals(ticker="F", total_debt=f.total_debt,
                                             total_cash=f.total_cash, ebit=[10e9]))
    assert ratio * 10e9 == pytest.approx(dc.net_debt.value)
    assert enterprise_value(Fundamentals(ticker="F", market_cap=50e9, total_debt=f.total_debt,
                                         total_cash=f.total_cash)) == pytest.approx(50e9 + dc.net_debt.value)


def test_a_missing_cash_figure_still_shows_no_cash_line_and_says_gross_debt():
    dc = debt_and_cash(Fundamentals(ticker="X", financial_currency="USD", total_debt=5e9))
    lines = dc.lines()
    assert lines[0] == "owes $5.0bn net of cash (cash not reported, so this is gross debt)"
    assert "total debt $5.0bn" in lines
    assert not any(ln.startswith("cash and short-term") for ln in lines)
    assert not any("not stated —" in ln and ln.endswith("— ") for ln in lines)


def test_the_judge_pack_gives_three_dated_lines_and_drops_the_provider_net_debt(report):
    from aristos_council.judges.blind_pack import build_blind_pack
    # MSFT-shaped: the page's $52.0bn against the provider's statement line of $19.4bn
    f = Fundamentals(ticker="MSFT", name="Microsoft", financial_currency="USD", total_debt=154.0e9,
                     total_cash=102.0e9, operating_cash_flow=182.9e9, trailing_period_end="2026-06-30")
    report.check.debt_and_cash = debt_and_cash(f)
    statements = {"balance": {"periods": ["2025-06-30", "2026-06-30"],
                              "rows": {"Total Debt": [60.6e9, 154.0e9], "Net Debt": [12.9e9, 19.4e9],
                                       "Cash And Cash Equivalents": [30.2e9, 33.0e9]}}}
    pack = build_blind_pack(report, f, statements)
    section = dict(pack.sections)["Debt and cash"]
    assert section[:3] == ["owes $52.0bn net of cash (balance sheet of Jun 2026)",
                           "total debt $154.0bn (balance sheet of Jun 2026)",
                           "cash and short-term investments $102.0bn (balance sheet of Jun 2026)"]
    assert any(NET_DEBT_DEFINITION in ln for ln in section)
    balance = dict(pack.sections)["Balance sheet"]
    assert not any(ln.startswith("Net Debt:") for ln in balance)
    assert any(ln.startswith("Total Debt:") for ln in balance)
    assert "19.4" not in "\n".join(line for _, lines in pack.sections for line in lines)


# NET-DEBT-2 -----------------------------------------------------------------
def test_total_debt_is_the_statement_row_and_the_excess_is_a_separate_lease_figure():
    import pandas as pd
    from aristos_council.data.yfinance_adapter import _statement_total_debt
    q = pd.DataFrame({pd.Timestamp("2026-06-30"): [56.826e9]}, index=["Total Debt"])
    assert _statement_total_debt(q, 128.813e9) == (56.826e9, pytest.approx(71.987e9))          # MSFT
    assert _statement_total_debt(q, 56.826e9)[1] is None                                        # no excess
    assert _statement_total_debt(q, 56.9e9)[1] is not None
    assert _statement_total_debt(None, 128.8e9) == (128.8e9, None)                              # no row: headline
    assert _statement_total_debt(pd.DataFrame(), None) == (None, None)


def test_the_page_says_leases_add_and_does_not_count_them():
    f = Fundamentals(ticker="MSFT", financial_currency="USD", total_debt=56.8e9, total_cash=76.8e9,
                     lease_liabilities=72.0e9, operating_cash_flow=180e9, trailing_period_end="2026-06-30")
    dc = debt_and_cash(f)
    lines = dc.lines()
    assert lines[:4] == ["holds $20.0bn more cash than debt", "total debt $56.8bn",
                         "cash and short-term investments $76.8bn", "leases add $72.0bn (not counted as debt above)"]
    none = debt_and_cash(Fundamentals(ticker="F", financial_currency="USD", total_debt=163.3e9, total_cash=22.1e9))
    assert not any("leases add" in ln for ln in none.lines())
