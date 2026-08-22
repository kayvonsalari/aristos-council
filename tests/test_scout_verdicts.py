"""SCOUT-2/SCOUT-3 — the holdings source and the F-Score column in the scout job.

Three changes, all making existing things VISIBLE rather than adding decision logic:

  PART A  HOLDING-flagged sheet rows stop being discarded: they become a fourth
          graded source ("holdings"), window-free (a holding is watched
          continuously), in the SAME combined cohort, with their own output
          folder and an entry in the combined index. A non-equity holding (the
          watch table carries ETFs) is graded and EXCLUDED by the stock lenses'
          asset-kind gate with a named reason — correct behavior, pinned here.
  PART B  every graded entry carries the Piotroski F-Score as EVIDENCE: an
          ``f_score`` block in the JSON and an F-Score line in the markdown.
          STRICTLY DISPLAY-ONLY — a null score renders ABSTAIN, never 0.
  SCOUT-3 the owner's list lives on its own "Holdings" TAB (Name | Ticker | Type),
          not on the news tabs — part A's flag path harvested ZERO rows live. The
          tab is now the PRIMARY holdings input and the flag path a supplement:
          every row joins (no date window, no news requirement), the Ticker cell
          is the symbol VERBATIM after trim (numbers de-numbered, nothing mapped),
          a non-gradeable Type is skipped naming it, and the two inputs merge into
          one source de-duped by symbol with the TAB row winning a collision.

Deterministic: a fake adapter, no network, no LLM (the grading is ranker-only).
Nothing is written under the repo — the output test drives ``_write_outputs``
against a tmp root.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.tools.screening import piotroski_f_score

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
TODAY = date(2026, 8, 18)


def _module():
    spec = importlib.util.spec_from_file_location(
        "_scout_verdicts_cli", ROOT / "scripts" / "scout_verdicts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sv = _module()


# --------------------------------------------------------------------------- #
# Sheet fixtures — the scout sheet's column order: date, ticker, company,
# story, (2 unused), flags.
# --------------------------------------------------------------------------- #
def _row(day: str, ticker: str, company: str, story: str, flags: str = "") -> str:
    return f"{day},{ticker},{company},{story},,,{flags}"


SHEET = "\n".join([
    "Some preamble,,,,,,",
    "Date added,Ticker & listing,Company,Story,,,Flags",
    _row("2026-08-16", "A (NYSE)", "Alpha Inc", "Alpha story"),          # in window
    _row("2026-06-01", "OLD (NYSE)", "Old Inc", "stale story"),          # out of window
    _row("not-a-date", "Z (NYSE)", "Zeta Inc", "bad date"),              # skipped
    _row("2026-08-15", "Nothing parseable here", "No Ticker", "no sym"),  # skipped
    _row("2019-01-01", "B (NYSE)", "Bravo Inc", "held since 2019", "HOLDING"),
    _row("2020-02-02", "BNKE (NYSE)", "Bank ETF", "watch", "HOLDING - core"),
    _row("2021-03-03", "still nothing", "Mystery", "watch", "holding"),   # lowercase
])

# The dedicated Holdings TAB (SCOUT-3): Name | Ticker | Type, no date column at
# all. A title row above the header, a spacer row below the data — both survivable.
HOLDINGS_TAB = "\n".join([
    "My holdings,,",
    "Name,Ticker,Type",
    "Alpha Inc,A,Stock",                     # also scouted as NEWS on the FT tab
    "Bravo Inc, B ,Stock",                   # also HOLDING-flagged on the FT tab
    "Bank ETF,BNKE,ETF",                     # graded, then asset-kind excluded
    "Some Property Trust,TRST,Trust",        # the stock lenses cannot grade it
    "No Ticker Co,,Stock",                   # blank ticker cell
    "Hong Kong Co,1211.0,Stock",             # Sheets stored the code as a NUMBER
    ",,",
])


class _Adapter(MarketDataAdapter):
    """Three healthy equities plus one ETF (quote_type ETF → asset-kind gated)."""

    name = "fake"
    STOCKS = {
        "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
                  operating_income=[3000.0, 2800, 2600, 2400],
                  tax_provision=[600.0, 560, 520, 480],
                  pretax_income=[2900.0, 2700, 2500, 2300],
                  invested_capital=[5000.0] * 4,
                  total_revenue=[200.0, 170, 150, 120]),
        "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
                  operating_income=[1500.0, 1450, 1400, 1350],
                  tax_provision=[300.0, 290, 280, 270],
                  pretax_income=[1450.0, 1400, 1350, 1300],
                  invested_capital=[5000.0] * 4,
                  total_revenue=[150.0, 140, 130, 120]),
        "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
                  operating_income=[500.0, 490, 480, 470],
                  tax_provision=[100.0, 98, 96, 94],
                  pretax_income=[480.0, 470, 460, 450],
                  invested_capital=[5000.0] * 4,
                  total_revenue=[125.0, 120, 115, 110]),
    }
    # A earns all nine checks; B is deliberately thin (under the 5-check minimum,
    # so it ABSTAINS); C is fully computable and earns few.
    F_SERIES = {
        "A": dict(net_income=[100.0, 50.0], total_assets_annual=[1000.0, 900.0],
                  operating_cash_flow_annual=[150.0, 120.0],
                  long_term_debt_annual=[100.0, 200.0],
                  current_assets_annual=[400.0, 300.0],
                  current_liabilities_annual=[200.0, 250.0],
                  shares_outstanding_annual=[90.0, 100.0],
                  gross_profit_annual=[100.0, 75.0]),
        "B": dict(net_income=[10.0, 8.0], total_assets_annual=[500.0, 480.0]),
        "C": dict(net_income=[5.0, 6.0], total_assets_annual=[400.0, 380.0],
                  operating_cash_flow_annual=[3.0, 4.0],
                  long_term_debt_annual=[50.0, 40.0],
                  current_assets_annual=[100.0, 120.0],
                  current_liabilities_annual=[90.0, 80.0],
                  shares_outstanding_annual=[110.0, 100.0],
                  gross_profit_annual=[30.0, 35.0]),
    }

    def get_fundamentals(self, ticker):
        if ticker in self.STOCKS:
            return Fundamentals(ticker=ticker, name=f"{ticker} Inc.",
                                quote_type="EQUITY", **self.STOCKS[ticker],
                                **self.F_SERIES[ticker])
        if ticker == "BNKE":
            return Fundamentals(ticker=ticker, name="Bank ETF", quote_type="ETF",
                                net_expense_ratio=0.35, total_assets=5e8)
        return Fundamentals(ticker=ticker)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(300)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


# --------------------------------------------------------------------------- #
# PART A — HOLDING rows become the holdings source
# --------------------------------------------------------------------------- #
def test_holding_rows_route_to_the_holdings_source_ignoring_the_window():
    read = sv.read_scouted(SHEET, "ft", 8, TODAY)
    # 2019/2020 rows are YEARS outside the 8-day news window and are kept anyway
    assert [h["symbol"] for h in read.holdings] == ["B", "BNKE"]
    assert all(h["source"] == sv.HOLDINGS_SOURCE for h in read.holdings)
    # the sheet they came from and the verbatim flag ride along
    assert [h["sheet"] for h in read.holdings] == ["ft", "ft"]
    assert [h["flags"] for h in read.holdings] == ["HOLDING", "HOLDING - core"]
    # ...and they are NOT candidates of the news source
    assert "B" not in [s["symbol"] for s in read.scouted]


def test_unparseable_holding_row_lands_in_holdings_skipped_with_the_cell():
    read = sv.read_scouted(SHEET, "ft", 8, TODAY)
    assert len(read.holdings_skipped) == 1
    row = read.holdings_skipped[0]
    assert row["source"] == sv.HOLDINGS_SOURCE
    assert row["reason"] == "no parseable listed ticker"
    assert "still nothing" in row["cell"] and row["ticker_cell"] == "still nothing"
    # a bad holdings cell is never reported as a NEWS skip
    assert all("still nothing" not in json.dumps(s) for s in read.skipped)


def test_non_holding_behavior_is_unchanged():
    read = sv.read_scouted(SHEET, "ft", 8, TODAY)
    assert read.scouted == [{"source": "ft", "date_added": "2026-08-16",
                             "ticker_cell": "A (NYSE)", "company": "Alpha Inc",
                             "story": "Alpha story", "symbol": "A"}]
    assert read.skipped == [
        {"source": "ft", "cell": "not-a-date | Z (NYSE) | Zeta Inc",
         "reason": "unparseable date"},
        {"source": "ft", "date_added": "2026-08-15",
         "ticker_cell": "Nothing parseable here", "company": "No Ticker",
         "story": "no sym", "reason": "no parseable listed ticker"},
    ]


def test_sheet_without_a_header_still_reports_one_skip_and_no_holdings():
    read = sv.read_scouted("nothing,useful\n", "ft", 8, TODAY)
    assert read.scouted == [] and read.holdings == [] and read.holdings_skipped == []
    assert read.skipped[0]["reason"] == "no 'Date added' header found"


def test_holdings_symbols_enter_the_cohort_exactly_once_news_metadata_wins():
    news = {"ft": sv.read_scouted(SHEET, "ft", 8, TODAY),
            # the economist sheet holds B as a holding too, and scouts A as news
            "economist": sv.read_scouted(
                "\n".join(["Date added,Ticker & listing,Company,Story,,,Flags",
                           _row("2026-08-17", "A (NYSE)", "Alpha Inc", "econ story"),
                           _row("2015-05-05", "B (NYSE)", "Bravo", "held", "HOLDING")]),
                "economist", 8, TODAY)}
    holdings = list(sv.dedup([h for r in news.values() for h in r.holdings]).values())
    assert [h["symbol"] for h in holdings] == ["B", "BNKE"]      # B once, not twice

    news_meta = sv.dedup([s for r in news.values() for s in r.scouted])
    hold_meta = sv.dedup(holdings)
    all_meta = {**news_meta}
    for sym, m in hold_meta.items():
        all_meta.setdefault(sym, m)
    base = ["C"]
    cohort = base + [s for s in all_meta if s not in base]
    assert cohort == ["C", "A", "B", "BNKE"]
    assert len(cohort) == len(set(cohort))
    # a name scouted as news AND held keeps its NEWS metadata in the shared cohort
    assert all_meta["A"]["source"] == "ft"


def test_also_found_by_records_the_overlap_in_both_directions():
    by_source = {"ft": {"A": {"symbol": "A"}, "B": {"symbol": "B"}},
                 "economist": {"A": {"symbol": "A"}},
                 sv.SCANNER_SOURCE: {"X": {"symbol": "X"}},
                 sv.HOLDINGS_SOURCE: {"B": {"symbol": "B"}, "X": {"symbol": "X"}}}
    also = sv.also_found_by(by_source)
    assert also["ft"] == {"A": ["economist"], "B": ["holdings"]}
    assert also["holdings"] == {"B": ["ft"], "X": ["growth-scanner"]}
    assert also[sv.SCANNER_SOURCE] == {"X": ["holdings"]}
    assert also["economist"] == {"A": ["ft"]}


# --------------------------------------------------------------------------- #
# SCOUT-3 — the dedicated Holdings TAB is the primary holdings input
# --------------------------------------------------------------------------- #
def test_the_holdings_tab_is_configured_by_default():
    """The tab is the PRIMARY input, so it must be wired with NO env setup: SCOUT-2
    shipped a path that needed none and still found nothing, because the list was
    never on the news tabs."""
    if os.environ.get("SCOUT_SHEET_HOLDINGS"):
        return                                  # an override is the owner's call
    assert sv.SHEET_HOLDINGS_URL.startswith("https://docs.google.com/spreadsheets/")
    assert "format=csv" in sv.SHEET_HOLDINGS_URL
    assert "gid=7542599" in sv.SHEET_HOLDINGS_URL


def test_every_gradeable_tab_row_joins_with_no_date_window():
    read = sv.read_holdings_tab(HOLDINGS_TAB)
    assert [h["symbol"] for h in read.holdings] == ["A", "B", "BNKE", "1211"]
    assert all(h["source"] == sv.HOLDINGS_SOURCE for h in read.holdings)
    assert all(h["sheet"] == sv.HOLDINGS_TAB_SHEET for h in read.holdings)
    assert [h["holding_type"] for h in read.holdings] == ["Stock", "Stock", "ETF",
                                                          "Stock"]
    assert [h["company"] for h in read.holdings] == ["Alpha Inc", "Bravo Inc",
                                                     "Bank ETF", "Hong Kong Co"]
    # no date column exists on the tab, and no row is filtered for the lack of one
    assert all(h["date_added"] == "" for h in read.holdings)
    assert all(h["story"] == "" for h in read.holdings)


def test_an_old_date_or_no_date_still_joins_even_if_the_tab_grows_a_date_column():
    read = sv.read_holdings_tab("\n".join([
        "Name,Ticker,Type,Date added",
        "Ancient Co,ANC,Stock,2009-01-01",      # years outside any news window
        "Dateless Co,DTL,Stock,",               # no date at all
    ]))
    assert [h["symbol"] for h in read.holdings] == ["ANC", "DTL"]
    assert read.skipped == []


def test_the_ticker_cell_is_verbatim_apart_from_trim_and_de_numbering():
    assert sv.holdings_ticker("  EL.PA  ") == "EL.PA"      # trimmed, never mapped
    assert sv.holdings_ticker("BRK-B") == "BRK-B"
    assert sv.holdings_ticker("1211.HK") == "1211.HK"      # text: left alone
    assert sv.holdings_ticker("1211.0") == "1211"          # Sheets number → text
    assert sv.holdings_ticker("1,211") == "1211"
    assert sv.holdings_ticker("0700") == "0700"            # leading zero preserved
    assert sv.holdings_ticker("") is None
    assert sv.holdings_ticker("   ") is None
    # NO name→ticker mapping lives here — a wrong cell is fixed in the SHEET, so a
    # company name is passed through as typed rather than guessed into a symbol
    assert sv.holdings_ticker("Estee Lauder") == "Estee Lauder"


def test_a_non_gradeable_type_and_a_blank_ticker_are_skipped_never_guessed():
    read = sv.read_holdings_tab(HOLDINGS_TAB)
    by_reason = {r["reason"]: r for r in read.skipped}
    assert set(by_reason) == {"type 'Trust' not gradeable by the stock lenses",
                             "blank or unparseable ticker cell"}
    trust = by_reason["type 'Trust' not gradeable by the stock lenses"]
    assert trust["source"] == sv.HOLDINGS_SOURCE
    assert trust["cell"] == "Some Property Trust | TRST | Trust"   # verbatim row
    blank = by_reason["blank or unparseable ticker cell"]
    assert blank["cell"] == "No Ticker Co |  | Stock"
    assert blank["ticker_cell"] == ""
    # nothing skipped leaks into the graded rows
    assert "TRST" not in [h["symbol"] for h in read.holdings]


def test_the_tab_and_a_flag_row_for_one_symbol_merge_with_the_tab_row_winning():
    news = {"ft": sv.read_scouted(SHEET, "ft", 8, TODAY)}   # flags B and BNKE
    tab = sv.read_holdings_tab(HOLDINGS_TAB)                # lists A, B, BNKE, 1211
    flagged = [h for r in news.values() for h in r.holdings]
    rows = list(sv.dedup(tab.holdings + flagged).values())
    assert [h["symbol"] for h in rows] == ["A", "B", "BNKE", "1211"]  # B/BNKE once
    # the TAB row wins the metadata collision — it is the authoritative list
    b = next(h for h in rows if h["symbol"] == "B")
    assert b["sheet"] == sv.HOLDINGS_TAB_SHEET and b["holding_type"] == "Stock"
    assert "flags" not in b
    # the overlap with a NEWS source is still recorded in both directions
    also = sv.also_found_by({
        "ft": sv.dedup([s for r in news.values() for s in r.scouted]),
        sv.HOLDINGS_SOURCE: sv.dedup(rows)})
    assert also[sv.HOLDINGS_SOURCE] == {"A": ["ft"]}
    assert also["ft"] == {"A": ["holdings"]}


def test_a_tab_fetch_failure_degrades_to_one_skip_and_the_run_continues():
    def boom(url):
        raise RuntimeError("403 no access")

    read = sv.load_holdings_tab("https://sheet/csv", fetch=boom)
    assert read.holdings == []
    assert read.skipped == [{"source": sv.HOLDINGS_SOURCE,
                             "cell": "https://sheet/csv",
                             "reason": "fetch failed: 403 no access"}]


def test_an_unconfigured_or_headerless_tab_is_a_skip_not_a_crash():
    assert sv.load_holdings_tab("").skipped[0]["reason"].startswith(
        "no Holdings tab configured")
    headerless = sv.load_holdings_tab("u", fetch=lambda url: "nothing,useful\n")
    assert headerless.holdings == []
    assert headerless.skipped == [{"source": sv.HOLDINGS_SOURCE, "cell": "u",
                                  "reason": "no 'Ticker' header found"}]
    empty = sv.load_holdings_tab("u", fetch=lambda url: "Name,Ticker,Type\n")
    assert empty.holdings == [] and empty.skipped == []


# --------------------------------------------------------------------------- #
# PART B — the F-Score block (evidence, never judgment)
# --------------------------------------------------------------------------- #
def test_f_score_block_renders_score_out_of_nine():
    adapter = _Adapter()
    block = sv.f_score_block(piotroski_f_score(adapter.get_fundamentals("A")))
    assert block["score"] == 9 and block["display"] == "9/9"
    assert block["computed"] == 9 and block["unavailable"] == 0
    assert "F-Score 9/9" in block["note"]


def test_thin_data_abstains_and_never_renders_zero():
    adapter = _Adapter()
    block = sv.f_score_block(piotroski_f_score(adapter.get_fundamentals("B")))
    assert block["score"] is None and block["display"] == "ABSTAIN"
    assert block["computed"] < 5 and "abstained" in block["note"]
    assert block["display"] != "0/9"


def test_a_real_zero_is_a_score_not_an_abstention():
    # every check computable, none earned — 0/9 is a REAL score and must render
    zero = Fundamentals(
        ticker="Z", net_income=[-5.0, 10.0], total_assets_annual=[1000.0, 900.0],
        operating_cash_flow_annual=[-9.0, 5.0], long_term_debt_annual=[300.0, 100.0],
        current_assets_annual=[100.0, 300.0], current_liabilities_annual=[250.0, 200.0],
        shares_outstanding_annual=[120.0, 100.0], gross_profit_annual=[10.0, 75.0],
        total_revenue=[50.0, 200.0])
    block = sv.f_score_block(piotroski_f_score(zero))
    assert block["score"] == 0 and block["display"] == "0/9"


def test_no_fundamentals_renders_null_and_abstain():
    block = sv.f_score_block(None)
    assert block == {"score": None, "computed": 0, "unavailable": 9,
                     "display": "ABSTAIN",
                     "note": "F-Score not computed: no fundamentals"}


def test_f_scores_for_uses_the_adapter_once_per_symbol_and_survives_failures():
    calls: list[str] = []

    class _Counting(_Adapter):
        def get_fundamentals(self, ticker):
            calls.append(ticker)
            if ticker == "BOOM":
                raise RuntimeError("no data for you")
            return super().get_fundamentals(ticker)

    blocks = sv.f_scores_for(["A", "B", "BOOM"], _Counting())
    assert calls == ["A", "B", "BOOM"]                # no symbol fetched twice
    assert blocks["A"]["display"] == "9/9"
    assert blocks["B"]["display"] == "ABSTAIN"
    assert blocks["BOOM"]["score"] is None
    assert blocks["BOOM"]["display"] == "ABSTAIN"     # a fetch failure is not a 0
    assert "no data for you" in blocks["BOOM"]["note"]


# --------------------------------------------------------------------------- #
# Output shapes — the holdings tab, the f_score fields, the pinned publisher keys
# --------------------------------------------------------------------------- #
_PUBLISHER_ENTRY_KEYS = {"symbol", "source", "also_found_by", "company",
                         "date_added", "story", "display", "rank_sum", "graded",
                         "comparable", "lenses"}
_PUBLISHER_PAYLOAD_KEYS = {"run_date", "source", "cohort", "cohort_size",
                           "base_universe", "lenses", "scouted", "skipped"}
_PUBLISHER_INDEX_SOURCE_KEYS = {"scouted", "skipped", "cohort", "cohort_size",
                                "json", "md"}


def _written(tmp_path: Path):
    """A full write pass: grade a 4-name cohort (3 equities + 1 ETF holding) under
    the real stock lenses with the fake adapter, then write every output under
    ``tmp_path`` — never the repo.

    The holdings source carries BOTH of its inputs: a Holdings-TAB row (C, dateless,
    with its Type) and two HOLDING-flagged news rows (B, BNKE)."""
    adapter = _Adapter()
    cohort = ["A", "B", "C", "BNKE"]
    result = sv.grade_cohort(cohort, today=TODAY, adapter=adapter, freeze_dir=None)
    f_scores = sv.f_scores_for(cohort, adapter)
    ft_rows = [{"source": "ft", "symbol": "A", "company": "Alpha Inc",
                "date_added": "2026-08-16", "story": "Alpha story"}]
    hold_rows = [{"source": sv.HOLDINGS_SOURCE, "symbol": "C",
                  "company": "Charlie Inc", "date_added": "", "story": "",
                  "holding_type": "Stock", "sheet": sv.HOLDINGS_TAB_SHEET},
                 {"source": sv.HOLDINGS_SOURCE, "symbol": "B", "company": "Bravo Inc",
                  "date_added": "2019-01-01", "story": "held since 2019",
                  "flags": "HOLDING", "sheet": "ft"},
                 {"source": sv.HOLDINGS_SOURCE, "symbol": "BNKE",
                  "company": "Bank ETF", "date_added": "2020-02-02", "story": "watch",
                  "flags": "HOLDING - core", "sheet": "ft"}]
    per_source = {
        "ft": {"scouted": ft_rows, "skipped": [], "result": result,
               "cohort_size": len(cohort), "cohort": "combined",
               "also_found_by": {}},
        sv.SCANNER_SOURCE: {"scouted": [], "skipped": [], "result": result,
                            "cohort_size": len(cohort), "cohort": "combined",
                            "also_found_by": {}, "scan_file": "growth_scan_x.csv",
                            "scan_header": "# rules"},
        sv.HOLDINGS_SOURCE: {"scouted": hold_rows,
                             # one skip from each input: the tab's Trust row and the
                             # news tab's unparseable HOLDING cell
                             "skipped": [{"source": sv.HOLDINGS_SOURCE,
                                          "cell": "Some Property Trust | TRST | Trust",
                                          "reason": "type 'Trust' not gradeable by "
                                                    "the stock lenses"},
                                         {"source": sv.HOLDINGS_SOURCE,
                                          "cell": "2021 | still nothing | Mystery",
                                          "reason": "no parseable listed ticker"}],
                             "result": result, "cohort_size": len(cohort),
                             "cohort": "combined",
                             "also_found_by": {"B": ["ft"]}},
    }
    sv._write_outputs(TODAY, per_source, f_scores, root=tmp_path)
    return tmp_path / "reports" / "scout"


def test_holdings_source_gets_its_own_folder_and_an_index_entry(tmp_path):
    out = _written(tmp_path)
    stamp = TODAY.isoformat()
    for name in (f"{stamp}_verdicts.json", f"{stamp}_verdicts.md", "latest.json"):
        assert (out / "holdings" / name).is_file(), name
    index = json.loads((out / "latest.json").read_text())
    assert set(index["sources"]) == {"ft", sv.SCANNER_SOURCE, sv.HOLDINGS_SOURCE}
    holdings = index["sources"][sv.HOLDINGS_SOURCE]
    # the pinned publisher-read keys are all still there, unchanged in meaning
    assert _PUBLISHER_INDEX_SOURCE_KEYS <= set(holdings)
    assert holdings == {"scouted": 3, "skipped": 2, "cohort": "combined",
                        "cohort_size": 4,
                        "json": f"reports/scout/holdings/{stamp}_verdicts.json",
                        "md": f"reports/scout/holdings/{stamp}_verdicts.md"}
    assert set(index) == {"run_date", "base_universe", "lenses", "sources"}


def test_holdings_payload_keeps_the_publisher_shape_and_lists_its_skips(tmp_path):
    out = _written(tmp_path)
    payload = json.loads((out / "holdings" / "latest.json").read_text())
    assert _PUBLISHER_PAYLOAD_KEYS <= set(payload)
    assert payload["source"] == "holdings" and payload["cohort"] == "combined"
    assert payload["lenses"] == sv.STOCK_LENSES
    # the tab row first (authoritative list), then the flagged news rows
    assert [e["symbol"] for e in payload["scouted"]] == ["C", "B", "BNKE"]
    assert [s["reason"] for s in payload["skipped"]] == [
        "type 'Trust' not gradeable by the stock lenses",
        "no parseable listed ticker"]
    for entry in payload["scouted"]:
        assert _PUBLISHER_ENTRY_KEYS <= set(entry)
    # the overlap with FT is recorded, and the holdings row keeps its verbatim flag
    flagged = next(e for e in payload["scouted"] if e["symbol"] == "B")
    assert flagged["also_found_by"] == ["ft"]
    assert flagged["flags"] == "HOLDING"


def test_a_non_equity_holding_is_excluded_by_the_asset_kind_gate_with_a_reason(tmp_path):
    out = _written(tmp_path)
    payload = json.loads((out / "holdings" / "latest.json").read_text())
    etf = next(e for e in payload["scouted"] if e["symbol"] == "BNKE")
    assert etf["graded"] == 0 and etf["rank_sum"] is None
    for sid in sv.STOCK_LENSES:
        cell = etf["lenses"][sid]
        assert cell["status"] == "excluded"
        assert "asset kind" in cell["reason"] and "ETF" in cell["reason"]


def test_a_tab_sourced_holding_is_a_first_class_entry_labelled_as_tab_sourced(
        tmp_path):
    """SCOUT-3 output shape: a Holdings-TAB row is a normal graded entry — same keys,
    every lens present — identifiable as tab-sourced and carrying its Type."""
    out = _written(tmp_path)
    payload = json.loads((out / "holdings" / "latest.json").read_text())
    tab_row = next(e for e in payload["scouted"] if e["symbol"] == "C")
    flag_row = next(e for e in payload["scouted"] if e["symbol"] == "B")
    assert _PUBLISHER_ENTRY_KEYS <= set(tab_row)
    assert tab_row["sheet"] == sv.HOLDINGS_TAB_SHEET
    assert tab_row["holding_type"] == "Stock"
    # the tab row carries no date and no flag; the flagged row carries both
    assert tab_row["date_added"] == "" and "flags" not in tab_row
    assert flag_row["date_added"] == "2019-01-01" and flag_row["flags"] == "HOLDING"
    # every lens weighed in, and none excluded it for its asset KIND (the ETF row's
    # exclusion is about being an ETF, never about coming off the tab)
    assert set(tab_row["lenses"]) == set(sv.STOCK_LENSES)
    for sid in sv.STOCK_LENSES:
        assert "asset kind" not in (tab_row["lenses"][sid]["reason"] or "")

    text = (out / "holdings" / f"{TODAY.isoformat()}_verdicts.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    head_i = next(i for i, ln in enumerate(lines)
                  if ln.startswith("## ") and "(C)" in ln)
    assert "—" not in lines[head_i]              # dateless: no dangling em dash
    assert lines[head_i + 1] == "Type: Stock"
    # the tab is named in the preamble, and a non-gradeable Type is listed, not lost
    assert "Holdings tab" in text or "Name | Ticker | Type" in text
    assert "type 'Trust' not gradeable by the stock lenses" in text


def test_every_entry_carries_the_f_score_block(tmp_path):
    out = _written(tmp_path)
    holdings = json.loads((out / "holdings" / "latest.json").read_text())
    ft = json.loads((out / "ft" / "latest.json").read_text())
    by_symbol = {e["symbol"]: e for e in holdings["scouted"] + ft["scouted"]}

    assert by_symbol["A"]["f_score"]["score"] == 9
    assert by_symbol["A"]["f_score"]["display"] == "9/9"
    assert set(by_symbol["A"]["f_score"]) == {"score", "computed", "unavailable",
                                             "display", "note"}
    # the thin fixture abstains — null, never 0
    assert by_symbol["B"]["f_score"]["score"] is None
    assert by_symbol["B"]["f_score"]["display"] == "ABSTAIN"
    # the ETF has no statement series at all: still an abstention, still not a 0
    assert by_symbol["BNKE"]["f_score"]["score"] is None
    assert by_symbol["BNKE"]["f_score"]["display"] == "ABSTAIN"


def test_markdown_carries_an_f_score_line_next_to_the_lens_lines(tmp_path):
    out = _written(tmp_path)
    text = (out / "holdings" / f"{TODAY.isoformat()}_verdicts.md").read_text(encoding="utf-8")
    assert "# HOLDINGS scout verdicts" in text
    assert "EVIDENCE ONLY" in text and "no lens consumes it" in text
    assert "Flags: HOLDING" in text and "asset-kind gate" in text
    # one F-Score line per graded name, immediately after that name's lens lines
    lines = text.splitlines()
    for symbol, expected in (("B", "ABSTAIN"), ("BNKE", "ABSTAIN")):
        start = next(i for i, ln in enumerate(lines) if f"({symbol}) —" in ln)
        block = lines[start:start + 10]
        lens_lines = [ln for ln in block if ln.startswith(f"- **{sv.STOCK_LENSES[0]}")]
        f_lines = [ln for ln in block if ln.startswith("- **F-Score**")]
        assert lens_lines and f_lines, symbol
        assert block.index(f_lines[0]) > block.index(lens_lines[0])
        assert expected in f_lines[0]
    assert "- **F-Score**: 9/9" in (out / "ft" / f"{TODAY.isoformat()}_verdicts.md"
                                    ).read_text(encoding="utf-8")


def test_the_f_score_moves_no_verdict(tmp_path):
    """The proof that part B is display-only: the graded grid is identical whether
    or not the F-Score is computed."""
    adapter = _Adapter()
    cohort = ["A", "B", "C"]
    with_scores = sv.grade_cohort(cohort, today=TODAY, adapter=adapter, freeze_dir=None)
    sv.f_scores_for(cohort, adapter)
    again = sv.grade_cohort(cohort, today=TODAY, adapter=adapter, freeze_dir=None)
    rendered = [[c.render() for c in row.cells.values()] for row in with_scores.rows]
    assert rendered == [[c.render() for c in row.cells.values()] for row in again.rows]
    # and no lens has adopted min_f_score (the gate stays unarmed)
    for sid in sv.STOCK_LENSES:
        text = (STRAT_DIR / f"{sid}.yaml").read_text(encoding="utf-8")
        assert "min_f_score" not in text, sid
