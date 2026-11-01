"""INDEX-AGE-1 (2026-10-31 sweep): peer market caps were stale (1211.HK still $93.4bn after a 35% fall) with no
sign on the page. A one-line notice appears when the market index snapshot is more than 10 days old."""
import pytest
from datetime import date
from types import SimpleNamespace

from aristos_council.market_index import index_age_notice

pytestmark = pytest.mark.index_age


def test_sixteen_days_old_says_so():
    assert index_age_notice("2026-10-15", date(2026, 10, 31)) == "peer sizes from 2026-10-15; 16 days old"


def test_ten_days_or_less_is_silent_and_an_unreadable_date_asserts_nothing():
    assert index_age_notice("2026-10-21", date(2026, 10, 31)) == ""
    assert index_age_notice("2026-10-20", date(2026, 10, 31)) != ""
    assert index_age_notice("", date(2026, 10, 31)) == "" and index_age_notice("n/a", date(2026, 10, 31)) == ""


def test_a_saved_reports_age_is_its_age_on_the_day_it_ran():
    from aristos_council.company_story import index_age_line
    rep = SimpleNamespace(peer_snapshot={"snapshot": "2026-10-15"}, run_at="2026-10-31T12:00:00+00:00")
    assert index_age_line(rep) == "peer sizes from 2026-10-15; 16 days old"
    assert index_age_line(SimpleNamespace(peer_snapshot=None, run_at="2026-10-31T12:00:00+00:00")) == ""


def test_the_story_page_carries_the_notice_above_the_table():
    from aristos_council import company_story as cs
    assert "index_age_line(report)" in open(cs.__file__, encoding="utf-8").read()


def test_the_sweep_manifest_records_the_index_status():
    from aristos_council.sweep.runner import manifest_dict
    from aristos_council.sweep.sets import SweepSet
    m = manifest_dict(SweepSet(name="x", entries=()), [], flags={"judges": False, "council": False},
                      started="", ended="", git={}, workers=1, finished=True)
    assert "market_index" in m and isinstance(m["market_index"], str)
