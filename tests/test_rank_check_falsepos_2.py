"""RANK-CHECK-FALSEPOS-2 - a rank pattern never matches inside a number or a money/price
expression. Live, KO.US sweep 2026-10-22: the structural banner read 'A rank is missing its
cohort size: "68 /"' over the forward P/E line of the neutral context."""
from __future__ import annotations

import pytest

from aristos_council.narration_schema import validate_narration
from tests.test_narration_schema import _well_formed

# The KO.US neutral context, verbatim from the live report (every line, the " / " ones included).
KO_NEUTRAL_CONTEXT = [
    "KO's market cap as of the index snapshot dated 2026-10-15 is USD 379.7bn.",
    "The company's accounts are annual, for the fiscal year to December 2025, with all money figures in USD.",
    "Net debt stands at USD 27.9bn.",
    "The company earns 9.0 times its interest bill.",
    "It would take 3.8 years of operating cash flow to repay its debt, and 5.3 years of free cash flow.",
    "Revenue compounded at 7.7% a year over 5 years and 0.8% a year over 10 years; revenue grew in 6 of "
    "the 10 years reported.",
    "The free cash flow series, oldest first: FY2022 $9.5bn; FY2023 $9.7bn; FY2024 $4.7bn; FY2025 $5.3bn.",
    "The valuation band reads EV/EBIT 24.6 at the 97th percentile of KO's own 5-year range, based on 44 "
    "of 61 months.",
    "Forward P/E is 28.1x this year (USD 92.68 price / USD 3.30 consensus EPS) and 26.3x next year "
    "(USD 92.68 / USD 3.52 consensus EPS).",
    "24 analysts cover the stock: 12 strong buy, 7 buy, 4 hold, 1 sell, 0 strong sell. The average price "
    "target is USD 95.12, approximately 3% above the last close of 92.68.",
    "The last close of 92.68 is above the 50-day SMA of 88.58 and the 200-day SMA of 80.53; the stock is "
    "-1.3% off its 52-week high.",
]
KO_FORWARD_PE = KO_NEUTRAL_CONTEXT[8]


def _rank_codes(lines):
    n = _well_formed()
    n.neutral_context = list(lines)
    return [i.code for i in validate_narration(n, ranker_verdict="buy")
            if i.code in ("unparseable_rank", "impossible_rank")]


def test_the_ko_forward_pe_line_is_not_a_rank():
    assert _rank_codes([KO_FORWARD_PE]) == []


def test_no_line_of_the_ko_neutral_context_trips_a_rank_check():
    assert _rank_codes(KO_NEUTRAL_CONTEXT) == []


@pytest.mark.parametrize("line", [
    "(USD 92.68 / USD 3.52 consensus EPS)",
    "(EUR 142.80 / EUR 7.25)",
    "(CHF 92 / CHF 3)",
    "USD 92 / 3 is the multiple.",                   # a currency code before the first number
    "$92 / 3 on EPS.",                                # a currency symbol before the first number
    "a price of 1,234 / 56 consensus EPS",            # a thousands group is not a rank
    "the ratio 92.68 / 3",                            # digits after a decimal point are not a rank
    "92 / price",
    "92 / EPS of 3.30",
])
def test_a_number_or_money_expression_is_never_a_rank(line):
    assert _rank_codes([line]) == []


@pytest.mark.parametrize("line, code", [
    ("It placed 3/ in the cohort.", "unparseable_rank"),
    ("It was ranked 4 / on growth.", "unparseable_rank"),
    ("It ranks 9/6 on growth.", "impossible_rank"),
    ("It ranks 7 / 0 here.", "impossible_rank"),
])
def test_genuine_bad_ranks_still_fire(line, code):
    assert code in _rank_codes([line])


def test_a_good_rank_next_to_a_money_figure_still_parses():
    assert _rank_codes(["It ranks 3/6 at a USD 92.68 price."]) == []
