"""B24-D4 - a Forensic "doubted" that stands on ONE of three tests says so in words. The rule is untouched."""
from aristos_council.company_cards import lens_rows, summary_chips
from aristos_council.company_report import LensVote, build_agreement
from aristos_council.company_story import answer_lines, story_paragraphs, table_rows
from tests.test_company_story import _report, _rank

NOTE = " (on one test only; two had no data)"


def _forensic(verdict="sell", measured=1, total=3):
    return LensVote("forensic_v1", "Forensic", kind="check", status="ranked", verdict=verdict, position=9,
                    cohort_size=16, factors_measured=measured, factors_total=total,
                    factor_note=f" \u00b7 ranked on {measured} of {total} factors")


def test_doubted_on_one_of_three_says_so_plainly():
    v = _forensic()
    assert v.thin_check_note == NOTE
    assert v.result().startswith(f"doubted{NOTE} - 9th of 16")


def test_other_cases_are_untouched():
    for kw in (dict(verdict="hold"), dict(verdict="buy"), dict(measured=2), dict(measured=3),
               dict(measured=1, total=1)):
        assert _forensic(**kw).thin_check_note == "", kw
    voting = LensVote("q_v1", "Quality", status="ranked", verdict="sell", position=9, cohort_size=16,
                      factors_measured=1, factors_total=3)
    assert voting.thin_check_note == ""                       # only a CHECK lens's mark
    assert _forensic(measured=1, total=4).thin_check_note == " (on one test only; three had no data)"


def test_the_agreement_the_answer_the_chips_and_the_table_all_say_it():
    rep = _report([_rank("Quality", "hold", 3), _forensic()])
    assert rep.agreement.checks["Forensic"] == "doubted"          # the mark itself is unchanged
    assert rep.agreement.check_notes["Forensic"] == NOTE
    assert f"Forensic reads doubted{NOTE}." in answer_lines(rep)[1]
    assert f"Forensic marks it doubted{NOTE} and does not vote." in dict(story_paragraphs(rep))["What happened."]
    assert f">Forensic: doubted{NOTE}<" in summary_chips(rep)
    assert next(r for r in table_rows(rep) if r.lens == "Forensic").outcome.startswith(f"doubted{NOTE}")
    assert next(r for r in lens_rows(rep) if r["lens"] == "Forensic")["detail"].endswith(NOTE)


def test_a_fully_measured_doubt_reads_as_before():
    rep = _report([_rank("Quality", "hold", 3), _forensic(measured=3)])
    assert rep.agreement.check_notes == {} and "one test only" not in answer_lines(rep)[1]


def test_votes_from_multi_carries_the_counts(tmp_path):
    from tests.test_company_report import RAW, _run
    rep = _run([RAW], tmp_path=tmp_path)
    ranked = next(v for v in rep.votes if v.ranked)
    assert ranked.factors_total >= 1 and ranked.factors_measured <= ranked.factors_total
