"""BANK-PACK-1 - for a bank or insurer the judge pack carries no plain cash-flow figure.

Live (JPM, 2026-10-22 sweep): OpenAI made "Operating cash flow was -$147.8bn in FY2025" its driving
fact. The company page already says "free cash flow: not meaningful for banks and insurers
(deposits and loans are the business)"; the pack now says the same, with the page's own words and
the page's own test (``abs_readings.is_financial_sector``)."""
from __future__ import annotations

import dataclasses

import pytest

from aristos_council.abs_readings import NOT_MEANINGFUL_FOR_FINANCIALS, is_financial_sector
from aristos_council.judges.blind_pack import build_blind_pack
from tests.test_judge_panel import STRAT_DIR, _dated_fundamentals, report  # noqa: F401  (fixture)

pytestmark = pytest.mark.min_group(3)

YEARS = ["2025-12-31", "2024-12-31", "2023-12-31"]
STATEMENTS = {
    "income": {"periods": ["2023-12-31", "2024-12-31", "2025-12-31"],
               "rows": {"Total Revenue": [158.1e9, 177.6e9, 182.4e9]}},
    "balance": {"periods": ["2023-12-31", "2024-12-31", "2025-12-31"],
                "rows": {"Total Debt": [400.0e9, 410.0e9, 420.0e9]}},
    "cashflow": {"periods": ["2023-12-31", "2024-12-31", "2025-12-31"],
                 "rows": {"Operating Cash Flow": [12.97e9, -42.0e9, -147.8e9],
                          "Cash Flow From Continuing Operating Activities": [12.97e9, -42.0e9, -147.8e9],
                          "Capital Expenditure": [-8.9e9, -9.2e9, -9.8e9],
                          "Change In Working Capital": [5.0e9, -60.0e9, -190.0e9],
                          "Free Cash Flow": [4.07e9, -51.2e9, -157.6e9],
                          "Cash Dividends Paid": [-12.8e9, -14.8e9, -16.0e9]}},
}


def _fundamentals(sector):
    return dataclasses.replace(
        _dated_fundamentals(), sector=sector,
        aligned_annual={"total_revenue": [182.4e9, 177.6e9, 158.1e9],
                        "operating_cash_flow": [-147.8e9, -42.0e9, 12.97e9],
                        "free_cash_flow": [-157.6e9, -51.2e9, 4.07e9]},
        aligned_period_ends={k: YEARS for k in ("total_revenue", "operating_cash_flow", "free_cash_flow")})


def _text(report, sector):
    return build_blind_pack(report, _fundamentals(sector), STATEMENTS, None, strategies_dir=STRAT_DIR).text()


def test_a_bank_pack_carries_no_plain_operating_or_free_cash_flow_figure(report):
    f = _fundamentals("Financial Services")
    assert is_financial_sector(f)                     # the page's test says bank
    text = _text(report, "Financial Services")
    for figure in ("147.8", "157.6", "12.97", "13.0", "51.2", "42.0", "190", "9.8", "4.07", "4.1"):
        assert figure not in text, figure
    for line in ("Operating cash flow", "Free cash flow", "Operating Cash Flow",
                 "Cash Flow From Continuing Operating Activities", "Capital Expenditure",
                 "Change In Working Capital", "Free Cash Flow"):
        assert f"- {line}: {NOT_MEANINGFUL_FOR_FINANCIALS}" in text, line
    # the other lines are untouched
    assert "Cash Dividends Paid:" in text and "-$16.0bn" in text and "Revenue (oldest first" in text


def test_a_non_bank_pack_is_unchanged(report):
    text = _text(report, "Technology")
    assert NOT_MEANINGFUL_FOR_FINANCIALS not in text
    assert ("Operating cash flow (oldest first, each figure labelled with its year): FY2023 $13.0bn · "
            "FY2024 -$42.0bn · FY2025 -$147.8bn") in text
    assert "- Operating Cash Flow: $13.0bn | -$42.0bn | -$147.8bn" in text
    assert "- Free Cash Flow: $4.1bn | -$51.2bn | -$157.6bn" in text


def test_a_missing_sector_is_not_a_bank(report):
    text = _text(report, None)
    assert NOT_MEANINGFUL_FOR_FINANCIALS not in text and "-$147.8bn" in text
