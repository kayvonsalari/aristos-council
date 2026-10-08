"""B25-1 (MIN-GROUP-5, Kayvon's ruling): a lens that ranks fewer than five companies gives no verdict;
a lens that ranks five to nine still votes, with a plain "small group: N companies" warning beside the vote.
MSFT 2026-10-08: Growth ranked 3 (no verdict), Value + Momentum ranked 6 (votes, with the warning)."""
from aristos_council.company_report import LensVote, build_agreement
from aristos_council.rank_engine import (MIN_RANKABLE_COHORT, SMALL_GROUP_MAX, small_group_text,
                                         too_few_to_rank_text)


def test_the_line_is_five_and_the_warning_band_is_five_to_nine():
    assert MIN_RANKABLE_COHORT == 5 and SMALL_GROUP_MAX == 9
    assert [small_group_text(n) for n in (3, 4)] == ["", ""]
    assert small_group_text(5) == "small group: 5 companies"
    assert small_group_text(9) == "small group: 9 companies"
    assert small_group_text(10) == ""


def test_the_too_few_wording_is_short_and_counts_companies():
    assert too_few_to_rank_text(3) == "too few to rank (3 companies)"
    assert too_few_to_rank_text(1) == "too few to rank (1 company)"


def _vote(label, sid, status, cohort, verdict="hold", pos=2):
    return LensVote(strategy_id=sid, label=label, status=status, cohort_size=cohort,
                    verdict=verdict if status == "ranked" else "", position=pos if status == "ranked" else None)


def test_a_vote_on_six_names_carries_the_small_group_warning_beside_it():
    v = _vote("Value + Momentum", "magic_formula_momentum_v1", "ranked", 6, "sell", 3)
    assert v.small_group_note == "small group: 6 companies"
    assert "small group: 6 companies" in v.result()


def test_a_vote_on_ten_names_carries_no_warning():
    v = _vote("Quality", "quality_v1", "ranked", 13, "hold", 6)
    assert v.small_group_note == "" and "small group" not in v.result()


def test_a_check_lens_never_gets_the_warning():
    v = LensVote(strategy_id="forensic_v1", label="Forensic", status="ranked", cohort_size=6, verdict="hold",
                 position=3, kind="check")
    assert v.small_group_note == ""


def test_three_names_is_too_few_not_a_vote():
    thin = _vote("Growth", "growth_garp_v2", "too_few", 3)
    ok = _vote("Quality", "quality_v1", "ranked", 13, "hold", 6)
    ag = build_agreement([thin, ok])
    assert ag.n_voted == 1 and ag.n_not_applying == 1
    assert thin.result() == "too few to rank (3 companies)"
