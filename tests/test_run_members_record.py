"""Every run record carries the EXACT membership it graded (FUND-UI-2).

A saved universe is a plain, editable ticker list now — so its id alone dates badly:
``my_portfolio_v1`` in June and in August are different cohorts, and a rank position is
a statement about the names it was ranked AGAINST. The run stamps the member list plus
an order-insensitive fingerprint, which is what keeps a past run interpretable after the
list moved on. Recorded silently — no versioning ceremony in the UI.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.pipeline import run_rank_pipeline
from aristos_council.universe import adhoc_universe_id, member_hash

from tests.test_run_rank_pipeline import UNIVERSE, _Adapter

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def _run(universe=None, **kw):
    return run_rank_pipeline(
        list(universe if universe is not None else UNIVERSE), "magic_formula_v1",
        ranker_only=True, strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=date(2026, 6, 30), **kw)


# --------------------------------------------------------------------------- #
# member_hash — the fingerprint
# --------------------------------------------------------------------------- #
def test_member_hash_is_order_insensitive_and_normalizing():
    assert member_hash(["AAPL", "MSFT"]) == member_hash(["msft", "aapl"])
    assert member_hash(["AAPL", "AAPL", "MSFT"]) == member_hash(["AAPL", "MSFT"])


def test_member_hash_changes_when_a_name_is_added_or_dropped():
    base = member_hash(["AAPL", "MSFT"])
    assert member_hash(["AAPL", "MSFT", "NVDA"]) != base
    assert member_hash(["AAPL"]) != base


def test_adhoc_id_is_the_member_hash_with_its_prefix():
    # The ad-hoc id was already this fingerprint; member_hash just names it, so ad-hoc
    # ids stay byte-identical to every previously recorded run.
    assert adhoc_universe_id(["AAPL", "MSFT"]) == f"adhoc:{member_hash(['AAPL', 'MSFT'])}"


# --------------------------------------------------------------------------- #
# The run record
# --------------------------------------------------------------------------- #
def test_run_meta_records_the_exact_ticker_list_graded():
    result = _run()
    assert result.meta["universe_members"] == UNIVERSE          # order as run, verbatim
    assert result.meta["universe_member_hash"] == member_hash(UNIVERSE)
    assert result.meta["universe_size"] == len(UNIVERSE)


def test_members_include_names_that_were_excluded_or_unrateable():
    # The graded cohort is what went IN, not what survived — a rank position is relative
    # to the whole list, so the record must not shrink to the ranked names.
    result = _run()
    ranked = {r.ticker for r in result.ranked}
    assert ranked < set(result.meta["universe_members"])
    assert "DEAD" in result.meta["universe_members"]            # unrateable, still a member


def test_two_runs_of_the_same_named_list_share_a_member_hash():
    a, b = _run(universe_id="my_list_v1"), _run(universe_id="my_list_v1")
    assert a.meta["universe_member_hash"] == b.meta["universe_member_hash"]
    assert a.meta["universe_id"] == b.meta["universe_id"] == "my_list_v1"


def test_editing_a_list_keeps_the_id_but_changes_the_member_hash():
    # THE case this exists for: the same saved list, edited between runs. The id is
    # unchanged (history stays linkable), the fingerprint is not (the cohort differs).
    before = _run(universe_id="my_list_v1")
    after = _run(universe=[t for t in UNIVERSE if t != "C"], universe_id="my_list_v1")
    assert before.meta["universe_id"] == after.meta["universe_id"]
    assert before.meta["universe_member_hash"] != after.meta["universe_member_hash"]
    assert "C" in before.meta["universe_members"]
    assert "C" not in after.meta["universe_members"]
