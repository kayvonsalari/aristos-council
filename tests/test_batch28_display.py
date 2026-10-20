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
