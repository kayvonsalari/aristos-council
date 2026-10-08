"""B25-7 - MSFT's badges come from "Systems Software" tests, but this run ranked it against a broad tech-sector group
(step 4, including chip makers). One line under the lens table says so."""
from types import SimpleNamespace

from aristos_council.company_report import NO_COHORT_TRACK_RECORD_LINE
from aristos_council.company_story import badge_group_note

CAPTION = "Track record from the Systems Software cohort, 10 years to Sep 2026"
LINE = ("Track record is from tests on Systems Software companies; this run compared it with a wider sector "
        "group, so the badges are a weaker guide.")


def _report(broad, caption=CAPTION):
    return SimpleNamespace(track_record_caption=caption, peer_group=SimpleNamespace(broad=broad))


def test_a_wider_sector_group_with_a_tested_industry_says_so():
    assert badge_group_note(_report(True)) == LINE


def test_a_normal_peer_group_adds_nothing():
    assert badge_group_note(_report(False)) == ""


def test_no_tested_cohort_adds_nothing():
    assert badge_group_note(_report(True, caption="")) == ""
    assert badge_group_note(_report(True, caption=NO_COHORT_TRACK_RECORD_LINE)) == ""


def test_no_peer_group_adds_nothing():
    assert badge_group_note(SimpleNamespace(track_record_caption=CAPTION, peer_group=None)) == ""
