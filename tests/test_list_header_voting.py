"""B22-B10 - "7 lenses x 3 names - 3 of 3 ranked by at least one" when only the non-voting Forensic ranked
them. Only voting lenses count as ranking a name."""
from aristos_council.pipeline import multi_summary_line
from tests.test_merged_multi_report import _multi
from tests.test_multi_strategy_run import RAW, SCREENED
import pytest  # noqa: E402

# B25-1: built under the old three-name rule; every test ABOUT the five-name rule is in test_min_group_5.py
pytestmark = pytest.mark.min_group(3)



def _only_a_check_ranked():
    res = _multi([RAW, SCREENED])
    for row in res.rows:
        row.cells[SCREENED].status = "excluded"
        row.cells[RAW].is_check = True            # RAW plays Forensic here: ranked, but it does not vote
    return res


def test_a_list_only_a_check_lens_ranked_says_no_voting_lens_could_rank_it():
    res = _only_a_check_ranked()
    ranked_by_check = sum(1 for r in res.rows if r.cells[RAW].status == "ranked")
    line = multi_summary_line(res)
    assert "no voting lens could rank these names" in line
    assert f"a check, ranked {ranked_by_check})" in line
    assert "ranked by at least one" not in line


def test_the_ordinary_list_header_is_unchanged():
    res = _multi([RAW, SCREENED])
    line = multi_summary_line(res)
    ranked = sum(1 for r in res.rows if any(c.status == "ranked" for c in r.cells.values()))
    assert f"{ranked} of {res.meta['universe_size']} ranked by at least one" in line
    assert "no voting lens" not in line


def test_a_name_ranked_only_by_a_check_is_not_counted_among_the_voting_rankings():
    res = _multi([RAW, SCREENED])
    first = res.rows[0]
    for sid in (SCREENED,):
        first.cells[sid].status = "excluded"
    first.cells[RAW].is_check = True
    others = sum(1 for r in res.rows[1:] if any(c.status == "ranked" and not c.is_check
                                                  for c in r.cells.values()))
    assert f"{others} of {res.meta['universe_size']} ranked by at least one" in multi_summary_line(res)
