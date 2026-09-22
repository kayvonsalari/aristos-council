"""GAP-LEDGER-1 step 3 — the headlines, the fence around the model, and the Todoist task.

The fence is the important half. This system lets an LLM speak in exactly one place, and
these tests pin what it cannot do: it cannot add a name, it cannot be asked about a name
with no headlines, it cannot be handed a figure to restate, and when it fails the run still
produces its numbers with the failure STATED rather than an absent line nobody can tell from
a line that was never asked for.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aristos_council.gap_ledger.config import DEFAULT_CONFIG, NY
from aristos_council.gap_ledger.explain import (NO_REASON, SYSTEM_PROMPT, build_prompt,
                                                explanations)
from aristos_council.gap_ledger.ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow
from aristos_council.gap_ledger.news import (Headline, eodhd_symbol, gather_news,
                                             headlines_from_rows, news_window,
                                             parse_timestamp, publisher_from_link)
from aristos_council.gap_ledger.todoist import (PROJECT_NAME, deliver, task_body,
                                                task_title)

from .gap_ledger_fakes import FakeNews, FakeRunner

RUN_AT = datetime(2026, 9, 22, 9, 0, tzinfo=NY)


# --------------------------------------------------------------------------- #
# news
# --------------------------------------------------------------------------- #
def test_the_symbol_gets_the_us_suffix_and_an_explicit_one_is_left_alone():
    assert eodhd_symbol("aapl") == "AAPL.US"
    assert eodhd_symbol("BMW.XETRA") == "BMW.XETRA"


def test_the_publisher_is_derived_from_the_link_and_nothing_else():
    """EODHD's news rows carry no publisher field. The host is a fact about the link; a
    publisher name would be an invention."""
    assert publisher_from_link("https://www.reuters.com/x/y") == "reuters.com"
    assert publisher_from_link("") == ""


def test_the_lookback_is_the_configured_number_of_hours():
    since, until = news_window(RUN_AT)
    assert until == RUN_AT
    assert (until - since) == timedelta(hours=DEFAULT_CONFIG.news_lookback_hours)


@pytest.mark.parametrize("raw, expected_hour", [("2026-09-22T11:30:00+00:00", 11),
                                                ("2026-09-22 11:30:00", 11)])
def test_a_naive_stamp_is_read_as_utc_rather_than_guessed_local(raw, expected_hour):
    when = parse_timestamp(raw)
    assert when is not None and when.astimezone(timezone.utc).hour == expected_hour


def test_an_unparseable_stamp_is_none_rather_than_a_guess():
    assert parse_timestamp("yesterday morning") is None
    assert parse_timestamp(None) is None


def test_rows_outside_the_window_are_dropped_and_rows_inside_are_kept():
    since, until = news_window(RUN_AT)
    rows = [{"title": "inside", "link": "https://a.com/1",
             "date": (until - timedelta(hours=2)).isoformat()},
            {"title": "too old", "link": "https://a.com/2",
             "date": (since - timedelta(hours=5)).isoformat()}]
    kept = headlines_from_rows(rows, since=since, until=until)
    assert [h.title for h in kept] == ["inside"]


def test_a_row_with_no_usable_stamp_is_kept_because_the_query_was_windowed():
    since, until = news_window(RUN_AT)
    kept = headlines_from_rows([{"title": "no date", "link": "https://a.com/1"}],
                              since=since, until=until)
    assert [h.title for h in kept] == ["no date"]


def test_headlines_come_back_newest_first():
    since, until = news_window(RUN_AT)
    rows = [{"title": "older", "link": "https://a.com/1",
             "date": (until - timedelta(hours=6)).isoformat()},
            {"title": "newer", "link": "https://a.com/2",
             "date": (until - timedelta(hours=1)).isoformat()}]
    assert [h.title for h in headlines_from_rows(rows, since=since, until=until)] == [
        "newer", "older"]


def test_a_malformed_payload_is_no_headlines_rather_than_an_exception():
    since, until = news_window(RUN_AT)
    assert headlines_from_rows({"error": "nope"}, since=since, until=until) == []
    assert headlines_from_rows([{"link": "https://a.com"}], since=since, until=until) == []


def test_news_off_answers_nothing_for_every_name_without_calling_anything():
    out = gather_news(["AAA", "BBB"], source=None, run_at=RUN_AT)
    assert set(out) == {"AAA", "BBB"}
    assert all(not n.matched and not n.related for n in out.values())


def test_news_is_asked_for_the_configured_window_per_name():
    source = FakeNews(by_ticker={"AAA": [Headline(title="AAA rises", link="https://a.com",
                                                 symbols=("AAA.US",))]})
    out = gather_news(["AAA", "BBB"], source=source, run_at=RUN_AT)
    assert [t for t, _, _ in source.asked] == ["AAA", "BBB"]
    assert all(until - since == timedelta(hours=18) for _, since, until in source.asked)
    assert out["BBB"].found == "no news found"
    assert out["AAA"].found == "news found"


# --------------------------------------------------------------------------- #
# the fence around the model
# --------------------------------------------------------------------------- #
def _headline(ticker_note="Alpha beats") -> Headline:
    return Headline(title=ticker_note, link="https://a.com/1",
                    published_at=RUN_AT - timedelta(hours=2), source="a.com")


class _Answer:
    def __init__(self, pairs):
        self.lines = [type("L", (), {"ticker": t, "line": v})() for t, v in pairs]


def test_no_runner_means_no_reason_line_at_all(monkeypatch):
    """GAP-NEWS-MATCH-1: with --explain off there is no per-row line. ``NO_REASON`` is
    reserved for a run that ASKED and came back empty — printing it on an off run implied a
    search that never happened."""
    out = explanations(["AAA"], {"AAA": [_headline()]}, runner=None)
    assert out.lines == {}
    assert out.called is False


def test_a_name_with_no_headlines_is_never_put_to_the_model():
    runner = FakeRunner(answer=_Answer([("AAA", "something")]))
    out = explanations(["AAA"], {"AAA": []}, runner=runner)
    assert runner.calls == []
    assert out.lines["AAA"] == NO_REASON
    assert "no headlines for any candidate" in out.note


def test_only_names_with_headlines_appear_in_the_prompt():
    runner = FakeRunner(answer=_Answer([("AAA", "Alpha reported results (https://a.com/1)")]))
    explanations(["AAA", "BBB"], {"AAA": [_headline()], "BBB": []}, runner=runner)
    _system, user = runner.calls[0]
    assert "AAA" in user and "BBB" not in user


def test_the_prompt_carries_no_figure_for_the_model_to_restate():
    """The gap and the relative volume are the deterministic screen's. The model is handed
    headlines, so there is no number in scope for it to recompute or contradict."""
    prompt = build_prompt({"AAA": [_headline()]})
    assert "gap" not in prompt.lower()
    assert "%" not in prompt
    assert "https://a.com/1" in prompt


def test_the_model_cannot_add_a_name_to_the_list():
    runner = FakeRunner(answer=_Answer([("AAA", "Alpha reported (https://a.com/1)"),
                                        ("ZZZ", "Zeta soared (https://z.com)")]))
    out = explanations(["AAA"], {"AAA": [_headline()]}, runner=runner)
    assert set(out.lines) == {"AAA"}


def test_a_name_the_model_skipped_keeps_the_no_reason_marker():
    runner = FakeRunner(answer=_Answer([("AAA", "Alpha reported (https://a.com/1)")]))
    out = explanations(["AAA", "BBB"], {"AAA": [_headline()], "BBB": [_headline("Beta")]},
                       runner=runner)
    assert out.lines["BBB"] == NO_REASON


def test_a_model_failure_degrades_with_a_stated_note_rather_than_taking_the_run_down():
    """Silence is the one unacceptable outcome: an absent line cannot be told from a line
    that was never asked for."""
    runner = FakeRunner(raise_with="503 overloaded")
    out = explanations(["AAA"], {"AAA": [_headline()]}, runner=runner)
    assert out.lines == {"AAA": NO_REASON}
    assert out.called is True
    assert "model call failed" in out.note and "503" in out.note


def test_the_system_prompt_forbids_advice_and_forbids_outside_knowledge():
    text = SYSTEM_PROMPT.format(no_reason=NO_REASON)
    assert "Use ONLY the headlines given" in text
    assert "Never say whether to buy, sell or hold" in text
    assert NO_REASON in text


# --------------------------------------------------------------------------- #
# Todoist
# --------------------------------------------------------------------------- #
def _candidate(ticker="AAA") -> LedgerRow:
    return LedgerRow(ticker=ticker, group=GROUP_CANDIDATE, gap_pct=0.082,
                     relative_volume=6.4, spread_note="spread ok",
                     news_found="news found", headline="Alpha beats",
                     news_source="a.com", news_link="https://a.com/1",
                     reason="Alpha reported results above expectations (https://a.com/1)")


def test_the_task_title_names_the_day_the_count_and_the_names():
    title = task_title(RUN_AT.date(), [_candidate("AAA"), _candidate("BBB")])
    assert "2026-09-22" in title and "2 name(s)" in title and "AAA, BBB" in title


def test_the_task_body_carries_the_gap_the_volume_the_flags_and_the_link():
    body = task_body([_candidate()])
    assert "+8.2%" in body and "6.4x" in body
    assert "spread ok" in body and "news found" in body
    assert "https://a.com/1" in body
    assert "no recommendation" in body.lower()


def test_an_empty_day_sends_nothing():
    """A daily "no candidates" task trains you to ignore the project."""
    from .gap_ledger_fakes import FakeTodoist
    client = FakeTodoist()
    outcome = deliver(RUN_AT.date(), [LedgerRow(ticker="X", group=GROUP_BASELINE)],
                      client=client)
    assert not outcome.sent
    assert client.tasks == []
    assert "no candidates" in outcome.skipped


def test_the_project_is_created_when_it_does_not_exist_and_says_so():
    from .gap_ledger_fakes import FakeTodoist
    client = FakeTodoist()
    outcome = deliver(RUN_AT.date(), [_candidate()], client=client)
    assert outcome.sent and outcome.project_created
    assert client.created_projects == [PROJECT_NAME]
    assert "project created" in outcome.sentence()


def test_an_existing_project_is_reused():
    from .gap_ledger_fakes import FakeTodoist
    client = FakeTodoist(projects={PROJECT_NAME: "proj-9"})
    outcome = deliver(RUN_AT.date(), [_candidate()], client=client)
    assert outcome.sent and not outcome.project_created
    assert client.tasks[0]["project_id"] == "proj-9"


def test_a_delivery_failure_is_reported_and_never_raised():
    """The CSV is the record; Todoist is a convenience. An unsent task is reported, not
    silent, and not fatal."""
    from .gap_ledger_fakes import FakeTodoist
    outcome = deliver(RUN_AT.date(), [_candidate()],
                      client=FakeTodoist(fail_on="create_task"))
    assert not outcome.sent
    assert "NOT sent" in outcome.sentence()
    assert "refused the task" in outcome.error


def test_delivery_switched_off_is_distinct_from_an_empty_day():
    outcome = deliver(RUN_AT.date(), [_candidate()], client=None)
    assert not outcome.sent and not outcome.error
    assert "switched off" in outcome.skipped
