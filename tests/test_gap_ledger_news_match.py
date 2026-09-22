"""GAP-NEWS-MATCH-1 — a story belongs to a name only on positive evidence.

Live, 2026-09-22, every one of these is a real wrong attribution from the first full run:

* one **AXT Inc.** headline arrived as news for **AXTI, CPRI and DK** at once;
* **ONON** got a Quest Diagnostics / Labcorp article;
* **JAZZ** got an Iambic Therapeutics story;
* **BMRN** got Travere; **VCYT** got DGX;
* **ALNY**, up 28%, showed no news at all.

The cause is that EODHD's ``symbols`` is a LOOSE tag list and a windowed query on one ticker
returns anything tagged with it. So the rule is now: the provider's own primary symbol, or
the ticker in the headline, or the company's name in the headline. Everything else is kept in
the record as "related, not matched" and never printed as the name's news — a wrong reason
beside a real gap is worse than no reason, because it gets believed.

The regression cases below are named after the tickers they actually broke on.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from aristos_council.gap_ledger.config import NY, at_ny
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, read_day
from aristos_council.gap_ledger.news import (MATCH_NAME, MATCH_PRIMARY, MATCH_TICKER,
                                             RELATED, Headline, base_symbol,
                                             headlines_from_rows, match_news, match_reason,
                                             name_core, news_window)
from aristos_council.gap_ledger.run import format_report, run_screen

from .gap_ledger_fakes import (FakeBars, FakeNews, daily_series, intraday_window,
                               prior_sessions)

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))


def _story(title: str, *symbols: str) -> Headline:
    return Headline(title=title, link="https://x.com/1", source="x.com",
                    published_at=RUN_AT - timedelta(hours=2), symbols=symbols)


# --------------------------------------------------------------------------- #
# the live mis-attributions, each as a regression
# --------------------------------------------------------------------------- #
AXT = _story("AXT Inc. Announces Third Quarter Results", "AXTI.US", "CPRI.US", "DK.US")


def test_the_axt_story_belongs_to_axti():
    assert match_reason(AXT, "AXTI", "AXT Inc.") == MATCH_PRIMARY


@pytest.mark.parametrize("ticker, company", [("CPRI", "Capri Holdings Limited"),
                                             ("DK", "Delek US Holdings, Inc.")])
def test_the_axt_story_does_not_belong_to_the_other_tagged_names(ticker, company):
    """The exact bug: one headline, three names, two of them wrong."""
    assert match_reason(AXT, ticker, company) == ""


def test_onon_does_not_get_a_quest_labcorp_article():
    story = _story("Quest Diagnostics and Labcorp expand testing", "DGX.US", "ONON.SW")
    assert match_reason(story, "ONON", "On Holding AG") == ""


def test_vcyt_does_not_get_a_dgx_article():
    story = _story("Quest Diagnostics raises outlook", "DGX.US", "VCYT.US")
    assert match_reason(story, "VCYT", "Veracyte, Inc.") == ""


def test_bmrn_does_not_get_a_travere_article():
    story = _story("Travere Therapeutics reports FDA decision", "TVTX.US", "BMRN.US")
    assert match_reason(story, "BMRN", "BioMarin Pharmaceutical Inc.") == ""


def test_jazz_does_not_get_an_iambic_article_it_is_merely_tagged_on():
    story = _story("Iambic Therapeutics doses first patient", "IMBC.US", "JAZZ.US")
    assert match_reason(story, "JAZZ", "Jazz Pharmaceuticals plc") == ""


def test_a_genuine_alnylam_story_does_match():
    """ALNY showed no news while up 28%. A real Alnylam story matches on its name."""
    story = _story("Alnylam reports positive Phase 3 data", "ALNY.US")
    assert match_reason(story, "ALNY", "Alnylam Pharmaceuticals, Inc.") == MATCH_PRIMARY
    tagged_elsewhere = _story("Alnylam reports positive Phase 3 data", "PFE.US", "ALNY.US")
    assert match_reason(tagged_elsewhere, "ALNY",
                        "Alnylam Pharmaceuticals, Inc.") == MATCH_NAME


# --------------------------------------------------------------------------- #
# the three ways in
# --------------------------------------------------------------------------- #
def test_the_providers_primary_symbol_counts():
    assert match_reason(_story("Something happened", "AAPL.US"), "AAPL") == MATCH_PRIMARY


def test_the_ticker_in_the_headline_counts():
    assert match_reason(_story("SHOP jumps on guidance"), "SHOP") == MATCH_TICKER


def test_the_company_name_in_the_headline_counts():
    assert match_reason(_story("Shopify raises guidance"), "SHOP",
                        "Shopify Inc.") == MATCH_NAME


def test_a_ticker_is_matched_as_a_whole_word_not_a_fragment():
    """"AXT" must not match inside "AXTI"."""
    assert match_reason(_story("AXTI soars 12%"), "AXT", "") == ""
    assert match_reason(_story("AXT soars 12%"), "AXT", "") == MATCH_TICKER


def test_a_one_or_two_letter_ticker_is_not_looked_for_in_prose():
    """"T" or "F" would match almost any sentence. Those names still match on the primary
    symbol and on their company name, which is the stronger signal anyway."""
    assert match_reason(_story("Ford recalls 100,000 trucks"), "F", "") == ""
    assert match_reason(_story("Ford recalls 100,000 trucks"), "F",
                        "Ford Motor Company") == MATCH_NAME
    assert match_reason(_story("Ford recalls trucks", "F.US"), "F", "") == MATCH_PRIMARY


def test_the_exchange_suffix_is_not_part_of_the_identity():
    assert base_symbol("ONON.SW") == "ONON"
    assert match_reason(_story("x", "ONON.SW"), "ONON") == MATCH_PRIMARY


# --------------------------------------------------------------------------- #
# the company-name core
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, core", [
    ("AXT Inc.", "axt"),
    ("Alnylam Pharmaceuticals, Inc.", "alnylam pharmaceuticals"),
    ("Jazz Pharmaceuticals plc", "jazz pharmaceuticals"),
    ("Delek US Holdings, Inc.", "delek us"),
    ("Shopify Inc.", "shopify"),
])
def test_legal_furniture_is_stripped_from_the_name(name, core):
    assert name_core(name) == core


def test_a_name_that_strips_to_nothing_usable_switches_the_test_off():
    """"On Holding AG" leaves "on", which would match half the English language. Returning
    "" disables the company-name test rather than matching on a fragment."""
    assert name_core("On Holding AG") == ""
    assert name_core("The Group") == ""
    assert match_reason(_story("Monday trading was quiet"), "ONON", "On Holding AG") == ""


# --------------------------------------------------------------------------- #
# the split
# --------------------------------------------------------------------------- #
def test_matched_and_related_are_both_kept_and_newest_first():
    news = match_news([AXT, _story("Capri Holdings beats", "CPRI.US")], "CPRI",
                      "Capri Holdings Limited")
    assert [h.title for h in news.matched] == ["Capri Holdings beats"]
    assert [h.title for h in news.related] == [AXT.title]
    assert news.how == MATCH_PRIMARY
    assert news.found == "news found"


def test_only_related_stories_read_as_related_not_as_news_found():
    news = match_news([AXT], "CPRI", "Capri Holdings Limited")
    assert news.matched == ()
    assert len(news.related) == 1
    assert news.found == RELATED


def test_nothing_returned_reads_as_no_news_found():
    assert match_news([], "AAA", "Alpha Inc.").found == "no news found"


def test_the_provider_symbol_list_is_parsed_off_the_payload():
    since, until = news_window(RUN_AT)
    rows = [{"title": "AXT Inc. beats", "link": "https://a.com/1",
             "date": (until - timedelta(hours=1)).isoformat(),
             "symbols": ["AXTI.US", "CPRI.US"]}]
    parsed = headlines_from_rows(rows, since=since, until=until)
    assert parsed[0].symbols == ("AXTI.US", "CPRI.US")
    assert parsed[0].primary_symbol == "AXTI"


def test_a_payload_with_no_symbols_is_not_an_error():
    since, until = news_window(RUN_AT)
    parsed = headlines_from_rows([{"title": "t", "link": "https://a.com/1"}],
                               since=since, until=until)
    assert parsed[0].symbols == ()
    assert parsed[0].primary_symbol == ""


# --------------------------------------------------------------------------- #
# end to end: the record and the report
# --------------------------------------------------------------------------- #
def _world() -> FakeBars:
    return FakeBars(
        daily={t: daily_series(end=DAY, sessions=300, volume=3_000_000)
               for t in ("AXTI", "CPRI")},
        intraday={t: intraday_window(DAY, end=time(9, 0), price=55.0,
                                     volume_per_bar=10_000)
                     + prior_sessions(before=DAY, count=20,
                                      premarket_volume_per_bar=1_000)
                  for t in ("AXTI", "CPRI")})


def _run(tmp_path, **kwargs):
    bars = _world()
    return run_screen(pool=["AXTI", "CPRI"], pool_source="t", daily=bars, intraday=bars,
                      day=DAY, run_at=RUN_AT, root=tmp_path, **kwargs)


def test_the_wrongly_tagged_name_is_recorded_as_related_not_as_news(tmp_path):
    news = FakeNews(by_ticker={"AXTI": [AXT], "CPRI": [AXT]})
    _run(tmp_path, news_source=news,
         company_names={"AXTI": "AXT Inc.", "CPRI": "Capri Holdings Limited"})
    rows = {r.ticker: r for r in read_day(DAY, root=tmp_path)}
    assert rows["AXTI"].news_found == "news found"
    assert rows["AXTI"].news_match == MATCH_PRIMARY
    assert rows["AXTI"].headline == AXT.title

    assert rows["CPRI"].news_found == RELATED
    assert rows["CPRI"].news_match == ""
    assert rows["CPRI"].headline == ""          # never printed as CPRI's news
    assert rows["CPRI"].news_link == ""
    assert rows["CPRI"].related_count == 1
    assert rows["CPRI"].related_headline == AXT.title   # but KEPT in the record
    assert rows["CPRI"].related_link == AXT.link


def test_the_report_marks_a_related_only_name_rather_than_claiming_news(tmp_path):
    news = FakeNews(by_ticker={"AXTI": [AXT], "CPRI": [AXT]})
    text = format_report(_run(tmp_path, news_source=news,
                              company_names={"AXTI": "AXT Inc.",
                                             "CPRI": "Capri Holdings Limited"}))
    cpri = [line for line in text.splitlines() if line.strip().startswith("CPRI")][0]
    assert RELATED in cpri and "1 related" in cpri


def test_the_model_is_only_shown_matched_stories(tmp_path):
    """A wrong reason beside a real gap is worse than no reason."""
    from .gap_ledger_fakes import FakeRunner

    runner = FakeRunner(answer=type("A", (), {"lines": []})())
    news = FakeNews(by_ticker={"AXTI": [AXT], "CPRI": [AXT]})
    _run(tmp_path, news_source=news, explain_runner=runner,
         company_names={"AXTI": "AXT Inc.", "CPRI": "Capri Holdings Limited"})
    _system, user = runner.calls[0]
    assert "AXTI" in user
    assert "CPRI" not in user


# --------------------------------------------------------------------------- #
# --explain off says so once, not per row
# --------------------------------------------------------------------------- #
def test_with_explain_off_the_header_says_so_once_and_no_row_carries_a_reason(tmp_path):
    news = FakeNews(by_ticker={"AXTI": [AXT]})
    result = _run(tmp_path, news_source=news, company_names={"AXTI": "AXT Inc."})
    text = format_report(result)
    assert "Reason line: off" in text
    assert "no clear reason found" not in text
    assert all(row.reason == "" for row in read_day(DAY, root=tmp_path))


def test_no_clear_reason_found_is_reserved_for_an_explain_run_that_found_nothing(tmp_path):
    from .gap_ledger_fakes import FakeRunner
    from aristos_council.gap_ledger.explain import NO_REASON

    runner = FakeRunner(answer=type("A", (), {"lines": []})())
    news = FakeNews(by_ticker={"AXTI": [AXT]})
    result = _run(tmp_path, news_source=news, explain_runner=runner,
                  company_names={"AXTI": "AXT Inc."})
    assert result.explain.called
    assert result.explain.lines["AXTI"] == NO_REASON
    assert NO_REASON in format_report(result)


# --------------------------------------------------------------------------- #
# the SHORT name — what the live run actually needed
# --------------------------------------------------------------------------- #
def test_a_headline_may_use_the_short_name():
    """The defect the ALNY and F cases exposed: the full core alone matched nothing, because
    a headline says "Alnylam reports", never "Alnylam Pharmaceuticals, Inc. reports"."""
    from aristos_council.gap_ledger.news import name_forms

    assert name_forms("Alnylam Pharmaceuticals, Inc.") == ("alnylam pharmaceuticals",
                                                           "alnylam")
    assert match_reason(_story("Alnylam reports positive Phase 3 data"), "ALNY",
                        "Alnylam Pharmaceuticals, Inc.") == MATCH_NAME
    assert match_reason(_story("Ford recalls 100,000 trucks"), "F",
                        "Ford Motor Company") == MATCH_NAME


def test_an_ordinary_leading_word_is_not_matched_on_its_own():
    """"American" is English before it is a company. AAL still matches on two words."""
    from aristos_council.gap_ledger.news import name_forms

    assert name_forms("American Airlines Group Inc.") == ("american airlines",)
    assert match_reason(_story("American consumers cut spending"), "AAL",
                        "American Airlines Group Inc.") == ""
    assert match_reason(_story("American Airlines cuts capacity"), "AAL",
                        "American Airlines Group Inc.") == MATCH_NAME


def test_a_generic_first_word_still_matches_on_its_two_word_form():
    from aristos_council.gap_ledger.news import name_forms

    assert name_forms("Capital One Financial Corporation") == ("capital one financial",
                                                               "capital one")
    assert match_reason(_story("Capital One raises card losses outlook"), "COF",
                        "Capital One Financial Corporation") == MATCH_NAME
    assert match_reason(_story("Capital flows into bonds"), "COF",
                        "Capital One Financial Corporation") == ""


def test_a_short_single_word_name_is_not_matched_alone():
    """A three-letter first word is not distinctive enough to risk in prose."""
    from aristos_council.gap_ledger.news import name_forms

    assert "axt" in name_forms("AXT Inc.")          # the whole core IS "axt"
    # "bp" is two characters, so it is never sought on its own; the longer forms remain.
    assert name_forms("BP Midstream Partners") == ("bp midstream partners", "bp midstream")
    assert match_reason(_story("BP profits fall"), "BPMP", "BP Midstream Partners") == ""
