"""B25-6 - MSFT's Growth lens read "matched its group - 0 of 0 years positive - no random-pick comparison - 0 monthly
test rounds". With no test rounds the line says only that there is no test history."""
from aristos_council.backtest import NO_TEST_HISTORY, Badge, track_record


def _badge(**kw):
    base = dict(label="untested here", verdict="insufficient", mean_excess=0.0, luck_pct=None, years_positive=0,
                years_measured=0, rounds_held=0)
    base.update(kw)
    return Badge(**base)


def test_no_rounds_says_only_that_there_is_no_test_history():
    b = _badge()
    assert b.detail_line() == "No test history for this lens in this industry yet." == NO_TEST_HISTORY
    for zero in ("0 of 0", "0 monthly", "random-pick", "matched its group"):
        assert zero not in b.detail_line()


def test_no_rounds_means_no_failed_test_and_no_why_line():
    assert _badge().failed_tests() == () and _badge().why_line() == ""


def test_a_badge_with_rounds_reads_as_before():
    b = Badge(label="no edge shown here", verdict="not proven", mean_excess=0.026, luck_pct=0.056,
              years_positive=5, years_measured=10, rounds_held=108)
    assert b.detail_line().startswith("beat its group by +2.6% a year on average · 5 of 10 years positive")
    assert "108 monthly test rounds" in b.detail_line()


def test_the_real_msft_case_growth_in_systems_software_has_no_history():
    b = track_record("tech_systems_software", "growth_garp_v2")
    assert b.years_measured == 0 and b.detail_line() == NO_TEST_HISTORY and b.note == NO_TEST_HISTORY
