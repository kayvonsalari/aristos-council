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
