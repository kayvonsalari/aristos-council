"""GAP-VIEWER-1 — what the viewer shows, tested without a browser.

The viewer's decisions live in ``gap_ledger.viewer`` as pure functions, so this file needs no
Streamlit at all. Three properties carry most of the weight:

* **one row per stock, and short** — nine columns in a fixed order, everything else in a
  details expander;
* **a data-source fact is said ONCE**, above the table, and never repeated down a column;
* **old CSVs render** — a file written before Batch 3 has no prints, one written before
  GAP-IBKR-1 has no ``source`` and no ``company``. Absent is absent: blank, or looked up,
  never a zero and never an error.

The tests use the ledger's own DTO with fields left unset, which is exactly what reading an
old CSV produces.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council.gap_ledger.ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow
from aristos_council.gap_ledger.verify import IBKR_UNAVAILABLE, SOURCE_IBKR, SOURCE_YFINANCE
from aristos_council.gap_ledger.viewer import (CARRIED_ON, COLUMNS, FACT_NO_SPREAD,
                                               FACT_NO_YF_VOLUME, FLAT, NO_NEWS,
                                               NOT_MEASURED, REVERSED, UNVERIFIED,
                                               VERIFIED_IBKR, candidates_of, company_of,
                                               day_summary, details_of, gap_cell,
                                               headline_cell, headline_of, order_rows,
                                               relative_volume_of, result_of, row_flags,
                                               source_facts, table_markdown, table_rows,
                                               verified_of)

DAY = date(2026, 9, 22)


def _row(**kwargs) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker="VKTX", company="Viking Therapeutics, Inc.",
                group=GROUP_CANDIDATE, gap_pct=0.2305, source=SOURCE_IBKR,
                relative_volume=1174.63, headline="Viking reports Phase 3 data",
                news_link="https://example.com/vktx", news_found="news found")
    base.update(kwargs)
    return LedgerRow(**base)


def _old_row(**kwargs) -> LedgerRow:
    """What reading a pre-IBKR, pre-Batch-3 CSV actually produces: most fields unset."""
    base = dict(date=DAY.isoformat(), ticker="AAPL", group=GROUP_CANDIDATE, gap_pct=0.04)
    base.update(kwargs)
    return LedgerRow(**base)


# --------------------------------------------------------------------------- #
# the company name
# --------------------------------------------------------------------------- #
def test_the_rows_own_company_name_is_used():
    assert company_of(_row()) == "Viking Therapeutics, Inc."


def test_an_old_csv_gets_the_name_looked_up():
    """No column in the file, so the viewer asks the index — the same source the run would
    have filled it from."""
    assert company_of(_old_row(), {"AAPL": "Apple Inc"}) == "Apple Inc"


def test_a_name_nobody_has_is_blank_and_not_an_error():
    assert company_of(_old_row(), {}) == ""
    assert company_of(_old_row(), None) == ""


def test_the_lookup_is_case_insensitive_on_the_ticker():
    assert company_of(_old_row(ticker="aapl"), {"AAPL": "Apple Inc"}) == "Apple Inc"


def test_the_rows_own_name_wins_over_the_lookup():
    """The file records what the run saw; a later index is not allowed to rewrite history."""
    row = _row(ticker="VKTX", company="Viking Therapeutics, Inc.")
    assert company_of(row, {"VKTX": "Something Else Plc"}) == "Viking Therapeutics, Inc."


# --------------------------------------------------------------------------- #
# the columns
# --------------------------------------------------------------------------- #
def test_the_column_order_is_the_specified_one():
    assert COLUMNS == ("Ticker", "Company", "Gap", "Verified", "Rel. volume", "Headline",
                       "Open", "Close", "Result")


def test_a_gap_up_is_green_and_a_gap_down_is_red():
    assert gap_cell(0.0576) == ":green[+5.76%]"
    assert gap_cell(-0.0310) == ":red[-3.10%]"


def test_a_flat_or_absent_gap_is_not_coloured():
    assert gap_cell(0.0) == "+0.00%"
    assert gap_cell(None) == "—"


def test_verified_says_ibkr_or_unverified():
    assert verified_of(_row(source=SOURCE_IBKR)) == VERIFIED_IBKR
    assert verified_of(_row(source=SOURCE_YFINANCE)) == UNVERIFIED


def test_an_old_csv_reads_as_unverified_which_is_true_of_it():
    """Nothing checked its volume, so "unverified" is not a guess."""
    assert verified_of(_old_row()) == UNVERIFIED


def test_relative_volume_reads_as_a_multiple_or_not_measured():
    assert relative_volume_of(_row(relative_volume=39.37)) == "39x"
    assert relative_volume_of(_row(relative_volume=None)) == NOT_MEASURED


def test_a_headline_is_a_link_and_no_headline_says_no_news():
    text, url = headline_of(_row())
    assert text == "Viking reports Phase 3 data" and url == "https://example.com/vktx"
    assert headline_of(_row(headline="", news_link=""))[0] == NO_NEWS
    assert headline_cell(_row()) == "[Viking reports Phase 3 data](https://example.com/vktx)"
    assert headline_cell(_row(headline="", news_link="")) == NO_NEWS


def test_a_headline_with_no_usable_link_is_still_shown_as_text():
    assert headline_cell(_row(news_link="")) == "Viking reports Phase 3 data"


@pytest.mark.parametrize("link", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x/y",
                                  "data:text/html,<script>"])
def test_only_http_links_are_ever_rendered_as_links(link):
    """A viewer that follows whatever scheme a news feed hands it has a hole in it."""
    assert headline_of(_row(news_link=link))[1] == ""
    assert "(" not in headline_cell(_row(news_link=link))


def test_a_headline_cannot_restructure_the_table():
    """Somebody else's prose must not be able to end a cell or forge a link."""
    nasty = _row(headline="A | B [x](http://evil) C", news_link="")
    assert headline_cell(nasty) == "A \\| B \\[x\\](http://evil) C"


def test_a_company_name_with_a_pipe_is_escaped():
    cells = table_rows([_row(company="A | B")])[0]
    assert cells[1] == "A \\| B"


# --------------------------------------------------------------------------- #
# the Result column
# --------------------------------------------------------------------------- #
def test_result_is_blank_before_outcomes_are_filled():
    assert result_of(_row(open_price=None, close_price=None)) == ""
    assert result_of(_row(open_price=100.0, close_price=None)) == ""


def test_a_gap_up_that_closed_higher_carried_on():
    assert result_of(_row(gap_pct=0.10, open_price=100.0, close_price=105.0)) == CARRIED_ON


def test_a_gap_up_that_closed_lower_reversed():
    assert result_of(_row(gap_pct=0.10, open_price=100.0, close_price=95.0)) == REVERSED


def test_a_gap_down_that_closed_lower_carried_on():
    assert result_of(_row(gap_pct=-0.10, open_price=100.0, close_price=95.0)) == CARRIED_ON


def test_a_gap_down_that_closed_higher_reversed():
    assert result_of(_row(gap_pct=-0.10, open_price=100.0, close_price=105.0)) == REVERSED


def test_an_exactly_flat_close_is_flat_and_not_a_reversal():
    """The scorecard counts flat as "did not carry on", conservatively. Here it gets its own
    word, because the two mean different things to someone reading a day."""
    assert result_of(_row(gap_pct=0.10, open_price=100.0, close_price=100.0)) == FLAT


def test_no_direction_means_no_result():
    assert result_of(_row(gap_pct=0.0, open_price=100.0, close_price=105.0)) == ""
    assert result_of(_row(gap_pct=None, open_price=100.0, close_price=105.0)) == ""


def test_the_result_agrees_with_the_scorecards_definition():
    """Same measurement, from the open in the gap's direction — the viewer and the scorecard
    must never disagree about a day."""
    from aristos_council.gap_ledger.score import continued

    for gap, close in ((0.10, 105.0), (0.10, 95.0), (-0.10, 95.0), (-0.10, 105.0)):
        row = _row(gap_pct=gap, open_price=100.0, close_price=close)
        assert (result_of(row) == CARRIED_ON) is (continued(row, "close_price") is True)


# --------------------------------------------------------------------------- #
# the order
# --------------------------------------------------------------------------- #
def test_ibkr_verified_names_come_first_then_the_largest_gap():
    rows = [_row(ticker="SMALLYF", source=SOURCE_YFINANCE, gap_pct=0.04),
            _row(ticker="BIGYF", source=SOURCE_YFINANCE, gap_pct=0.30),
            _row(ticker="SMALLIB", source=SOURCE_IBKR, gap_pct=0.04),
            _row(ticker="BIGIB", source=SOURCE_IBKR, gap_pct=0.20)]
    assert [r.ticker for r in order_rows(rows)] == ["BIGIB", "SMALLIB", "BIGYF", "SMALLYF"]


def test_the_gap_is_ordered_by_ABSOLUTE_size():
    rows = [_row(ticker="UP", gap_pct=0.05), _row(ticker="DOWN", gap_pct=-0.25)]
    assert [r.ticker for r in order_rows(rows)] == ["DOWN", "UP"]


def test_candidates_only_and_already_ordered():
    rows = [_row(ticker="CTRL", group=GROUP_BASELINE, gap_pct=0.9),
            _row(ticker="AAA", gap_pct=0.05), _row(ticker="BBB", gap_pct=0.30)]
    assert [r.ticker for r in candidates_of(rows)] == ["BBB", "AAA"]


def test_the_order_is_stable_for_equal_gaps():
    rows = [_row(ticker="ZZZ", gap_pct=0.10), _row(ticker="AAA", gap_pct=0.10)]
    assert [r.ticker for r in order_rows(rows)] == ["AAA", "ZZZ"]


# --------------------------------------------------------------------------- #
# said once
# --------------------------------------------------------------------------- #
def test_the_ibkr_banner_is_a_source_fact():
    facts = source_facts([_row(ibkr_note=IBKR_UNAVAILABLE, relative_volume=5.0)])
    assert facts == [IBKR_UNAVAILABLE]


def test_no_measured_volume_anywhere_is_a_source_fact():
    rows = [_row(ticker="A", relative_volume=None), _row(ticker="B", relative_volume=None)]
    assert FACT_NO_YF_VOLUME in source_facts(rows)


def test_one_measured_name_means_it_is_not_a_source_fact():
    """A fact about the run has to be true of the run."""
    rows = [_row(ticker="A", relative_volume=None), _row(ticker="B", relative_volume=39.0)]
    assert FACT_NO_YF_VOLUME not in source_facts(rows)


def test_a_subscription_spread_note_is_a_source_fact():
    rows = [_row(spread_note="spread unknown — IBKR market-data subscription does not cover "
                             "API streaming quotes")]
    assert FACT_NO_SPREAD in source_facts(rows)


def test_each_fact_appears_exactly_once_however_many_rows_carry_it():
    rows = [_row(ticker=f"T{n}", ibkr_note=IBKR_UNAVAILABLE, relative_volume=None)
            for n in range(20)]
    facts = source_facts(rows)
    assert facts.count(IBKR_UNAVAILABLE) == 1
    assert facts.count(FACT_NO_YF_VOLUME) == 1


def test_a_clean_day_has_no_banner():
    assert source_facts([_row(relative_volume=39.0, spread_note="spread ok")]) == []


# --------------------------------------------------------------------------- #
# per-row flags keep only what is about the row
# --------------------------------------------------------------------------- #
def test_a_wide_spread_is_a_row_flag():
    assert row_flags(_row(spread_note="wide spread 4.20%")) == ["wide spread 4.20%"]


def test_a_run_level_spread_note_is_not_a_row_flag():
    """It is in the banner; repeating it per row is what item 3 forbids."""
    note = "spread unknown — IBKR market-data subscription does not cover API streaming quotes"
    assert row_flags(_row(spread_note=note)) == []


def test_a_screen_note_is_a_row_flag_only_for_an_unverified_row():
    """An IB-verified row's yfinance screen note is not about anything any more."""
    assert row_flags(_row(source=SOURCE_YFINANCE,
                          screen_note="single pre-market print")) == ["single pre-market print"]
    assert row_flags(_row(source=SOURCE_IBKR, screen_note="single pre-market print")) == []


def test_related_but_unmatched_news_is_a_row_flag():
    assert "news related but not matched" in row_flags(_row(news_found="related, not matched"))


# --------------------------------------------------------------------------- #
# no per-row reason text
# --------------------------------------------------------------------------- #
def test_the_table_carries_no_reason_column_and_no_reason_text():
    """Item 2: no per-row reason below the table, and an old CSV's "no clear reason found"
    lines are not shown."""
    markdown = table_markdown([_row(reason="no clear reason found")])
    assert "no clear reason found" not in markdown
    assert "Reason" not in markdown


def test_the_reason_is_not_in_the_details_either():
    pairs = dict(details_of(_row(reason="no clear reason found")))
    assert "Reason" not in pairs
    assert "no clear reason found" not in pairs.values()


# --------------------------------------------------------------------------- #
# the table as a whole
# --------------------------------------------------------------------------- #
def test_the_table_has_a_header_a_rule_and_one_line_per_stock():
    markdown = table_markdown([_row(ticker="AAA"), _row(ticker="BBB")])
    lines = markdown.splitlines()
    assert lines[0].startswith("| Ticker | Company | Gap |")
    assert set(lines[1].strip("|").split("|")) == {"---"}
    assert len(lines) == 4


def test_every_row_has_exactly_nine_cells():
    for cells in table_rows([_row(), _old_row()]):
        assert len(cells) == len(COLUMNS)


def test_an_old_csv_row_renders_without_inventing_anything():
    cells = table_rows([_old_row()], {"AAPL": "Apple Inc"})[0]
    assert cells[0] == "AAPL"
    assert cells[1] == "Apple Inc"                        # looked up
    assert cells[3] == UNVERIFIED
    assert cells[4] == NOT_MEASURED                       # never measured, not zero
    assert cells[5] == NO_NEWS
    assert cells[6] == "—" and cells[7] == "—"            # no outcomes
    assert cells[8] == ""                                 # no result


# --------------------------------------------------------------------------- #
# the details expander
# --------------------------------------------------------------------------- #
def test_the_details_hold_what_the_table_left_out():
    row = _row(spread_pct=0.0004, premarket_prints=60, confirm_prints=6,
               confirm_average=37.0, ib_bid=109.9, ib_ask=110.1, price_1000=105.0,
               price_1130=106.0, premarket_vs_open=0.0078)
    labels = [name for name, _value in details_of(row)]
    for expected in ("Spread", "Pre-market prints", "Prints in the final 30 min",
                     "Confirmation average", "IB bid", "IB ask", "10:00 ET", "11:30 ET",
                     "Pre-market vs open"):
        assert expected in labels


def test_none_of_the_details_appear_in_the_table():
    row = _row(spread_pct=0.0004, premarket_prints=60, ib_bid=109.9)
    markdown = table_markdown([row])
    assert "0.04%" not in markdown and "109.9" not in markdown


def test_an_old_csv_has_a_short_details_list_rather_than_a_page_of_dashes():
    """A pair with nothing in it is omitted, not printed as a dash."""
    pairs = details_of(_old_row())
    assert pairs == [] or all(value not in ("", "—", "None") for _n, value in pairs)


def test_a_missing_value_is_omitted_rather_than_shown_as_zero():
    pairs = dict(details_of(_old_row()))
    assert "Pre-market prints" not in pairs
    assert "IB bid" not in pairs


# --------------------------------------------------------------------------- #
# the day summary (left panel, as far as the brief specifies it)
# --------------------------------------------------------------------------- #
def test_the_day_summary_counts_what_the_panel_shows():
    rows = [_row(ticker="A", source=SOURCE_IBKR, news_found="news found"),
            _row(ticker="B", source=SOURCE_YFINANCE, news_found="no news found"),
            _row(ticker="C", group=GROUP_BASELINE, source=SOURCE_YFINANCE)]
    counts = day_summary(rows)
    assert counts["logged"] == 3
    assert counts["candidates"] == 2
    assert counts["ibkr_confirmed"] == 1
    assert counts["unverified"] == 1
    assert counts["with_news"] == 1


def test_the_day_summary_counts_filled_outcomes():
    rows = [_row(ticker="A", close_price=100.0), _row(ticker="B")]
    assert day_summary(rows)["outcomes_filled"] == 1


def test_an_empty_day_summarises_to_zeroes_rather_than_raising():
    assert day_summary([])["candidates"] == 0
