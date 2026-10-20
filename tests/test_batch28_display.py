"""Batch 28 B - display and wording fixes. Unpinned: the five-name rule is live here."""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# B5 TOO-FEW-DISPLAY-1
# --------------------------------------------------------------------------- #
def test_the_lens_table_says_it_passed_and_why_there_is_no_rank():
    from aristos_council.company_report import LensVote
    from aristos_council.company_story import _outcome, _row_reason
    v = LensVote(strategy_id="x_v1", label="Growth", kind="selector", asks="", status="too_few", cohort_size=3)
    assert _outcome(v) == "passed, too few to rank"
    assert _row_reason(v) == "3 passed, under 5, no ranks"


# --------------------------------------------------------------------------- #
# B6 WOULD-RANK-GROUP-1
# --------------------------------------------------------------------------- #
def test_would_rank_names_its_group_and_the_lens_own_count():
    from aristos_council.shadow_rank import WouldRank
    five = WouldRank(True, position=1, cohort_size=28, lens_ranked=5).text
    assert "would rank 1st of the 28 names with usable figures (the lens itself ranked 5)" in five
    three = WouldRank(True, position=10, cohort_size=28, lens_ranked=3).text
    assert "(the lens itself ranked 3, too few to give anyone a rank)" in three
    assert "the lens itself" not in WouldRank(True, position=1, cohort_size=28).text   # built without a count


# --------------------------------------------------------------------------- #
# B7 FCF-UNITS-1
# --------------------------------------------------------------------------- #
def test_a_series_is_formatted_in_one_unit_scale():
    import re
    from aristos_council.tools.price_context import format_money_series
    shown = format_money_series([-13e6, 6.682e9, 6.739e9, 12.467e9], "USD")
    assert shown == ["-$0.01bn", "$6.68bn", "$6.74bn", "$12.47bn"]
    assert {re.sub(r"[^a-z]", "", s) for s in shown} == {"bn"}            # never "m" beside "bn"
    assert format_money_series([None, 5e6], "USD") == ["not available", "$5.0m"]
    assert format_money_series([2.5e6, 4.0e6], None) == ["2.5m", "4.0m"]


def test_ford_free_cash_flow_reads_in_one_scale_on_the_page_and_in_the_pack():
    from types import SimpleNamespace
    from aristos_council.data.adapter import Fundamentals
    from aristos_council.judges.blind_pack import _series
    f = Fundamentals(ticker="F", name="Ford", financial_currency="USD",
                     free_cash_flow_annual=[12.467e9, 6.739e9, 6.682e9, -13e6],
                     aligned_annual={"free_cash_flow": [12.467e9, 6.739e9, 6.682e9, -13e6]},
                     aligned_period_ends={"free_cash_flow": ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]},
                     period_ends={"free_cash_flow_annual": ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]})
    line = next(ln for ln in _series(f) if ln.startswith("Free cash flow"))
    assert "FY2022 -$0.01bn" in line and "m " not in line.split("year): ")[1] + " "
