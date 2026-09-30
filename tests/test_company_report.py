"""COMPANY REPORT (Part B + D) - one company against its peer group, on fabricated data.

Nothing here reaches the network, the real market index or a model: the adapter is a fake that
manufactures fundamentals from a ticker's number, the peer table is fabricated rows behind a tiny
store, and the summary runner is injected (or asserted never to be touched).

What is pinned: a fabricated peer table gives a deterministic vote; a lens that screens the company
out does not vote; no peer group means no votes and a stated reason (the band and the readings
survive); the summary is never called unless ticked; every export follows one page order; the run
is saved with the peer snapshot and every lens's ranks; and the Run tab's own ranks are untouched.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council.company_report import (SECTION_ORDER, LensVote, build_agreement,
                                            company_facts_pack, format_company_report,
                                            run_company_report)
from aristos_council.data.adapter import (Fundamentals, MarketDataAdapter, PriceBar,
                                          PriceHistory)
from aristos_council.export.report_html import company_report_html
from aristos_council.market_index import SOURCE_EODHD_LISTING, IndexRow

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
RAW = "magic_formula_raw_v1"
SCREENED = "magic_formula_v1"            # prefilters on min_roic 12%: a 6% company is screened OUT
TODAY = date(2026, 6, 30)
N_PEERS = 13


def _number(ticker: str) -> int:
    return 50 if ticker == "CO" else int(ticker[1:])


class _Adapter(MarketDataAdapter):
    """Fundamentals manufactured from the ticker: peers P00..P12 improve with their number, the
    company CO sits in the middle on EBIT but earns only ~6% on its capital."""

    name = "fake"

    def __init__(self, company_ebit: float = 400.0):
        self.company_ebit = company_ebit

    def get_fundamentals(self, ticker):
        n = _number(ticker)
        ebit = self.company_ebit if ticker == "CO" else 500.0 + 100.0 * n
        return Fundamentals(
            ticker=ticker, name=f"{ticker} Corp", company_name=f"{ticker} Corp",
            market_cap=2e10, sector="Technology", currency="USD", financial_currency="USD",
            ebit=[ebit], pe_ratio=10.0 + n / 4.0,
            operating_income=[ebit, ebit * 0.95, ebit * 0.9, ebit * 0.85],
            tax_provision=[ebit * 0.2] * 4, pretax_income=[ebit * 0.97] * 4,
            invested_capital=[5000.0] * 4, total_revenue=[900.0, 850, 800, 750],
            total_debt=1_000.0, total_cash=400.0, operating_cash_flow=250.0)

    def get_price_history(self, ticker, *, start, end):
        slope = 0.05 + 0.01 * _number(ticker)
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + slope * i, adj_close=100 + slope * i, volume=10)
            for i in range(300)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _row(ticker, code, *, name="", cap=2e10, sub="Semiconductors"):
    return IndexRow(
        ticker=ticker, yahoo_ticker=code, name=name or f"{code} Corp", exchange="US", market="US",
        currency="USD", sector="Technology", industry="Semiconductors",
        gics_sector="Information Technology", gics_industry="Semiconductors", gics_subindustry=sub,
        market_cap=cap, market_cap_usd=cap, market_cap_usd_source="computed",
        primary_ticker=ticker, isin=f"XX{abs(hash(ticker)) % 10**10:010d}", fetched_at="2026-09-26",
        source=SOURCE_EODHD_LISTING)


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def load(self):
        return list(self._rows)


def _table(n_peers=N_PEERS):
    return _Store([_row("CO.US", "CO", name="Company Co")]
                  + [_row(f"P{i:02d}.US", f"P{i:02d}") for i in range(n_peers)])


def _run(lens_ids, *, tmp_path, store=None, company_ebit=400.0, **kw):
    return run_company_report("CO", lens_ids, adapter=_Adapter(company_ebit),
                              strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                              store=store or _table(), **kw)


# =========================================================================== #
# votes
# =========================================================================== #
def test_a_fabricated_peer_table_gives_a_deterministic_vote(tmp_path):
    first = _run([RAW], tmp_path=tmp_path, save=False)
    second = _run([RAW], tmp_path=tmp_path, save=False)
    vote = first.votes[0]
    assert vote.ranked and vote.strategy_id == RAW and vote.votes
    assert vote.cohort_size == N_PEERS + 1                # the company and its 13 peers
    assert [v.result() for v in first.votes] == [v.result() for v in second.votes]
    assert vote.result() == f"{vote.word} - {vote.position}" + \
        {1: "st", 2: "nd", 3: "rd"}.get(vote.position % 10 if not 10 <= vote.position % 100 <= 20
                                        else 0, "th") + f" of {N_PEERS + 1}"
    assert vote.word in ("BUY", "HOLD", "SELL")


def test_the_lens_run_ranks_the_company_exactly_as_the_run_tab_ranks_that_list(tmp_path):
    """Ranks and verdicts in the Run tab are byte-identical: the vote IS the company's row of a
    plain ``run_rank_pipeline`` over [company + peers]."""
    from aristos_council.pipeline import run_rank_pipeline
    from aristos_council.rank_engine import cohort_positions

    report = _run([RAW], tmp_path=tmp_path, save=False)
    result = run_rank_pipeline(report.universe, RAW, ranker_only=True, adapter=_Adapter(),
                               today=TODAY, strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR)
    row = next(r for r in result.ranked if r.ticker == "CO")
    assert report.votes[0].verdict == row.verdict
    assert report.votes[0].position == cohort_positions(result.ranked)[row.ticker][0]


def test_a_lens_that_screens_the_company_out_does_not_vote(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    raw, screened = report.votes
    assert raw.ranked and not screened.ranked
    assert screened.status == "excluded"
    assert screened.result().startswith("does not apply - ")
    assert "return on invested capital" in screened.result()      # the screen's own reason
    ag = report.agreement
    assert ag.n_ticked == 2 and ag.n_voted == 1 and ag.n_not_applying == 1
    assert [label for label, _ in ag.not_applicable] == [screened.label]
    assert ag.headline.endswith("; 1 lens did not apply to this company")


def test_the_agreement_counts_equal_votes_and_a_check_marks_without_voting():
    votes = [LensVote("a", "Alpha", status="ranked", verdict="buy", position=2, cohort_size=14),
             LensVote("b", "Beta", status="ranked", verdict="hold", position=6, cohort_size=14),
             LensVote("c", "Gamma", status="excluded", reason="size floor"),
             LensVote("f", "Forensic", kind="check", status="ranked", verdict="sell", position=13,
                      cohort_size=14)]
    ag = build_agreement(votes, band_percentile=92.0)
    assert ag.n_ticked == 3 and ag.n_voted == 2            # the check is not a voter
    assert ag.buy == ("Alpha",) and ag.hold == ("Beta",) and ag.sell == ()
    assert ag.headline == "BUY on 1 of 2 votes; 1 lens did not apply to this company"
    assert ag.checks == {"Forensic": "doubted"}
    assert "doubted by Forensic" in ag.marks
    assert any(m.startswith("priced high: 92nd percentile") for m in ag.marks)
    row = ag.table_row("Company Co")
    assert row["BUY votes"] == "1 of 2" and row["Voted BUY"] == "Alpha"
    assert row["Checks"] == "Forensic: doubted"
    # no percentile (the band abstained) -> no band mark, and never a made-up one
    assert not any("priced high" in m for m in build_agreement(votes, band_percentile=None).marks)


def test_a_check_lens_speaks_its_own_words_and_never_votes(tmp_path):
    report = _run([RAW, "forensic_v1"], tmp_path=tmp_path, save=False)
    forensic = report.votes[1]
    assert not forensic.votes and forensic.role == "marks (does not vote)"
    if forensic.ranked:
        assert forensic.word in ("clean", "no concern", "doubted")
    assert report.agreement.n_ticked == 1 and "Forensic" in report.agreement.checks


# =========================================================================== #
# no peer group
# =========================================================================== #
def test_no_peer_group_gives_no_votes_and_a_reason_but_keeps_the_band_and_the_readings(tmp_path):
    lonely = _Store([_row("CO.US", "CO", name="Company Co", sub="Space Tourism")])
    lonely._rows[0].sector = "Space"
    lonely._rows[0].gics_sector = "Space"
    lonely._rows[0].industry = "Space Tourism"
    lonely._rows[0].gics_industry = "Space Tourism"
    report = _run([RAW, SCREENED], tmp_path=tmp_path, store=lonely, save=False)
    assert report.peer_group is not None and not report.peer_group.available
    assert report.no_vote_reason.startswith("no peer group, so no lens can vote")
    assert all(v.status == "no_group" and not v.ranked for v in report.votes)
    assert report.universe == []                             # nothing was ranked
    text = format_company_report(report)
    assert "No vote:" in text or "no peer group" in text
    assert "VALUATION BAND" in text and "ABSOLUTE READINGS" in text
    assert report.check.debt_and_cash is not None            # the readings survive
    assert report.check.valuation_band != "—"                # ...and so does the band


def test_a_company_missing_from_the_index_says_so_and_still_reports_its_readings(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, store=_Store([_row("P00.US", "P00")]), save=False)
    assert not report.peer_group.available
    assert "not in the market index" in report.no_vote_reason
    assert report.check.debt_and_cash is not None


def test_step_four_peers_are_ranked_like_any_other_and_the_page_says_it_is_broad(tmp_path):
    """A company whose industry is thin gets its sector; the votes are measured against that wider
    group and the report names it as such."""
    rows = [_row("CO.US", "CO", name="Company Co", sub="Only One Of Its Kind")]
    rows[0].industry = rows[0].gics_industry = "Only One Industry"
    rows += [_row(f"P{i:02d}.US", f"P{i:02d}", sub=f"Sub {i}") for i in range(N_PEERS)]
    for r in rows[1:]:
        r.industry, r.gics_industry = f"Industry {r.ticker}", f"GICS Industry {r.ticker}"
    report = _run([RAW], tmp_path=tmp_path, store=_Store(rows), save=False)
    assert report.peer_group.step == 4 and report.peer_group.broad
    assert report.votes[0].ranked
    assert "broad sector group - wider than a normal peer group" in format_company_report(report)


# =========================================================================== #
# the summary: opt-in, one call, never unless ticked
# =========================================================================== #
class _ExplodingRunner:
    model_id, temperature = "boom", 0.0

    def invoke(self, system, user):                        # pragma: no cover - must never run
        raise AssertionError("a model was called although the summary was not ticked")


def test_the_summary_is_never_called_unless_ticked(tmp_path, monkeypatch):
    import aristos_council.company_report as cr

    def _boom(*a, **k):                                     # pragma: no cover - must never run
        raise AssertionError("write_company_summary ran although the summary was not ticked")
    monkeypatch.setattr(cr, "write_company_summary", _boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill-if-used")
    report = _run([RAW, SCREENED], tmp_path=tmp_path, reader_runner=_ExplodingRunner(),
                  save=False)                                # with_summary defaults to False
    assert report.summary is None
    assert "SUMMARY" not in format_company_report(report).split("AGREEMENT")[0]
    assert "<h2>Summary</h2>" not in company_report_html(report)


def test_without_a_key_a_ticked_summary_says_so_and_calls_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = _run([RAW], tmp_path=tmp_path, with_summary=True, save=False)
    assert not report.summary.available
    assert "no API key" in report.summary.note
    assert "no API key" in format_company_report(report)


class _Writer:
    """A stand-in for the reader model: returns a fixed five-field summary, counts its calls."""

    model_id, temperature = "fake-reader", 0.0

    def __init__(self, fields):
        self.fields, self.calls = fields, 0

    def invoke(self, system, user):
        from aristos_council.agents.schemas import ReaderSummary
        self.calls += 1
        return ReaderSummary(**self.fields)


def _fields(report, extra=""):
    ag = report.agreement
    buy = ", ".join(ag.buy) or "no test"
    return dict(
        asked=f"This checks Company Co against {N_PEERS} similar companies under {len(report.votes)} tests.",
        happened=f"It was rated BUY by {buy}. {ag.headline}.{extra}",
        survived="Analysts are holding their profit forecasts steady.",
        doubt="One test may not apply.", cannot_say="This says nothing about the future.")


def test_a_ticked_summary_makes_one_call_and_is_published_when_it_passes(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    writer = _Writer(_fields(probe))
    report = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=writer, save=False)
    assert writer.calls == 1
    assert report.summary.available, report.summary.note
    text = format_company_report(report)
    assert text.index("SUMMARY") < text.index("AGREEMENT")   # the summary leads the page
    assert "Company Co" in text


def test_the_reader_withholds_a_number_that_is_not_on_the_page(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    writer = _Writer(_fields(probe, extra=" Its profit grew 777 percent."))
    report = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=writer, save=False)
    assert not report.summary.available
    assert "number not in the facts: 777" in report.summary.note
    assert writer.calls == 2                                  # one corrective retry, as the run summary


def test_the_reader_withholds_a_company_that_is_not_on_the_page(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    writer = _Writer(_fields(probe, extra=" Compare ACME."))
    report = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=writer, save=False)
    assert not report.summary.available and "name not in the run: ACME" in report.summary.note


def test_the_reader_withholds_a_lens_given_the_wrong_role(tmp_path):
    probe = _run([RAW, "forensic_v1"], tmp_path=tmp_path, save=False)
    fields = _fields(probe)
    fields["happened"] += " Forensic is a picker and counts as a vote."
    writer = _Writer(fields)
    report = _run([RAW, "forensic_v1"], tmp_path=tmp_path, with_summary=True,
                  reader_runner=writer, save=False)
    assert not report.summary.available and "role mismatch" in report.summary.note


def test_the_reader_withholds_a_buy_vote_it_leaves_unmentioned(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False, company_ebit=6000.0)
    assert probe.agreement.buy == (probe.votes[0].label,), "the strong company must be rated BUY"
    fields = _fields(probe)
    for key in ("asked", "happened", "survived", "doubt", "cannot_say"):
        fields[key] = fields[key].replace(probe.agreement.buy[0], "one test")
    report = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=_Writer(fields),
                  save=False, company_ebit=6000.0)
    assert not report.summary.available and "BUY vote not mentioned" in report.summary.note


def test_the_facts_pack_holds_only_what_the_page_prints(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    pack = company_facts_pack(report)
    assert set(pack) == {"company", "lenses", "agreement", "valuation_band", "absolute_readings",
                         "what_analysts_say"}
    assert pack["company"]["peer_group"]["step"] == 1
    assert [l["votes"] for l in pack["lenses"]] == [True, True]
    assert pack["agreement"]["buy_lenses_to_name"] == list(report.agreement.buy)
    assert pack["absolute_readings"]["debt_and_cash"] == report.check.debt_and_cash.lines()


# =========================================================================== #
# page order, exports, the saved run
# =========================================================================== #
_TEXT_HEADS = {"summary": "SUMMARY", "council opinion": "COUNCIL OPINION",
               "agreement": "AGREEMENT", "lens votes": "LENS VOTES",
               "peers": "PEERS", "valuation band": "VALUATION BAND",
               "absolute readings": "ABSOLUTE READINGS", "what analysts say": "WHAT ANALYSTS SAY",
               "sources": "SOURCES"}
_HTML_HEADS = {"summary": "<h2>Summary</h2>", "council opinion": "<h2>Council opinion</h2>",
               "agreement": "<h2>Agreement</h2>",
               "lens votes": "<h2>Lens votes</h2>", "peers": "<h2>Peers</h2>",
               "valuation band": "<h2>Valuation band</h2>",
               "absolute readings": "<h2>Absolute readings</h2>",
               "what analysts say": "<h2>What analysts say</h2>", "sources": "<h2>Sources</h2>"}


def test_the_page_order_is_the_same_in_the_text_and_the_html(tmp_path):
    from aristos_council.company_report import CouncilOpinion

    probe = _run([RAW], tmp_path=tmp_path, save=False)
    report = _run([RAW, SCREENED], tmp_path=tmp_path, with_summary=True,
                  reader_runner=_Writer(_fields(probe)), save=False)
    # COUNCIL-OPINION-1 — injected directly (no model call): the section's PLACEMENT is what
    # this test pins, not the council itself, which has its own dedicated tests below.
    report.council_opinion = CouncilOpinion(available=True, narrative="It ranked well.")
    assert SECTION_ORDER == ("summary", "council opinion", "agreement", "lens votes", "peers",
                             "valuation band", "absolute readings", "what analysts say",
                             "sources")
    text, html = format_company_report(report), company_report_html(report)
    for heads, doc in ((_TEXT_HEADS, text), (_HTML_HEADS, html)):
        at = [doc.index(heads[name]) for name in SECTION_ORDER]
        assert at == sorted(at), (heads, at)


def test_an_unticked_summary_leaves_no_section_and_the_order_holds(tmp_path):
    # Neither the summary nor the council opinion was ticked: BOTH optional sections are
    # absent, and the order holds over whatever remains.
    report = _run([RAW], tmp_path=tmp_path, save=False)
    text, html = format_company_report(report), company_report_html(report)
    order = [n for n in SECTION_ORDER if n not in ("summary", "council opinion")]
    assert "SUMMARY" not in text and "<h2>Summary</h2>" not in html
    assert "COUNCIL OPINION" not in text and "<h2>Council opinion</h2>" not in html
    for heads, doc in ((_TEXT_HEADS, text), (_HTML_HEADS, html)):
        at = [doc.index(heads[name]) for name in order]
        assert at == sorted(at)


def test_the_band_is_always_there_no_tick_box_no_not_requested(tmp_path):
    """BAND-ALWAYS-ON-1: the section is in the report every time - a reading, or an abstention with
    its reason - and there is no parameter to leave it out."""
    import inspect
    assert "with_valuation_band" not in inspect.signature(run_company_report).parameters
    report = _run([RAW], tmp_path=tmp_path, save=False)
    text, html = format_company_report(report), company_report_html(report)
    assert "VALUATION BAND" in text and "<h2>Valuation band</h2>" in html
    assert "not requested" not in text and "not requested" not in html
    assert report.check.valuation_band != "—"                 # computed, not skipped


def test_the_run_is_saved_under_runs_with_the_peer_snapshot_and_every_lens_ranks(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path)          # save=True (the default)
    directory = Path(report.saved_to)
    assert directory.parent == tmp_path / "runs" and "_company_check_CO" in directory.name
    record = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert record["kind"] == "company_report" and record["ticker"] == "CO"
    assert record["peer_snapshot"]["subject"] == "CO.US"
    assert len(record["peer_snapshot"]["members"]) == N_PEERS and record["peer_snapshot"]["step"] == 1
    assert set(record["lens_ranks"]) == {RAW, SCREENED}
    ranked = record["lens_ranks"][RAW]["ranked"]
    assert len(ranked) == N_PEERS + 1                          # the WHOLE list, peers included
    assert {"ticker", "position", "verdict", "combined_rank"} <= set(ranked[0])
    assert any(e["ticker"] == "CO" for e in record["lens_ranks"][SCREENED]["excluded"])
    assert record["votes"][1]["status"] == "excluded" and record["agreement"]["headline"]
    saved_text = (directory / "report.txt").read_text(encoding="utf-8")
    tail = "Ran in"                       # the saved file predates the "saved under" clause
    assert saved_text.split(tail)[0] == format_company_report(report).split(tail)[0]
    # the frozen-run reader must never mistake it for a replayable run
    assert not (directory / "manifest.json").exists()


def test_a_saved_company_report_is_not_picked_up_as_a_reference_run(tmp_path):
    from aristos_council.company_check import _latest_reference_run
    _run([RAW], tmp_path=tmp_path)
    assert _latest_reference_run(tmp_path / "runs", RAW, []) is None


def test_the_report_states_its_time_and_the_day_cache(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.seconds >= 0 and "Ran in" in format_company_report(report)


def test_an_unrateable_company_stops_at_the_reason(tmp_path):
    class _Dead(_Adapter):
        def get_fundamentals(self, ticker):
            return Fundamentals(ticker=ticker) if ticker == "CO" else super().get_fundamentals(ticker)

        def get_price_history(self, ticker, *, start, end):
            if ticker == "CO":
                raise RuntimeError("no timezone found, symbol may be delisted")
            return super().get_price_history(ticker, start=start, end=end)
    report = run_company_report("CO", [RAW], adapter=_Dead(), strategies_dir=STRAT_DIR,
                                universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                                store=_table(), save=False)
    assert report.unrateable and report.votes == [] or all(not v.ranked for v in report.votes)
    text = format_company_report(report)
    assert "UNRATEABLE" in text and "AGREEMENT" not in text
    assert "UNRATEABLE" in company_report_html(report)


def test_no_lens_ticked_says_so_and_still_reports_the_readings(tmp_path):
    report = _run([], tmp_path=tmp_path, save=False)
    assert report.votes == [] and report.agreement is None
    assert report.no_vote_reason == "No lens is ticked, so there is nothing to vote."
    assert report.check.debt_and_cash is not None


def test_importing_the_report_reaches_no_model_library():
    """In a subprocess, because by the time the rest of the suite has run langchain is in
    sys.modules for honest reasons and an in-process check would pass regardless."""
    import subprocess
    import sys
    code = ("import sys; import aristos_council.company_report; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('langchain', 'langchain_core', 'langchain_anthropic', 'anthropic', 'langgraph')); "
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parents[1]))
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


# =========================================================================== #
# the page renders the report (Streamlit AppTest, fabricated report, no network)
# =========================================================================== #
def _page():                                            # pragma: no cover - runs inside AppTest
    import streamlit as st

    import app
    app._render_company_report(st.session_state["_report"])


def test_the_page_renders_the_whole_report_in_order_and_offers_both_downloads(tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    probe = _run([RAW], tmp_path=tmp_path, save=False)
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False, with_summary=True,
                  reader_runner=_Writer(_fields(probe)))
    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["_report"] = report
    at.run()
    assert not at.exception
    heads = [str(getattr(h, "value", "")) for h in at.subheader]
    assert heads[:4] == ["Summary", "Agreement", "Lens votes", "Peers"], heads
    assert heads[-3:] == ["Absolute readings", "What analysts say", "Sources"], heads
    assert "Valuation band" in heads
    frames = [df.value for df in at.dataframe]
    assert any("BUY votes" in list(f.columns) for f in frames)            # the agreement row
    assert any("Result" in list(f.columns) for f in frames)               # the vote table
    votes = next(f for f in frames if "Result" in list(f.columns))
    assert any(str(r).startswith("does not apply - ") for r in votes["Result"])
    assert any("Market cap (USD)" in list(f.columns) for f in frames)     # the numeric peers table
    # the page carries no Streamlit-side model call: the summary came from the injected writer
    assert report.summary.available


def test_the_page_says_so_when_there_is_no_peer_group(tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    report = _run([RAW], tmp_path=tmp_path, store=_Store([_row("P00.US", "P00")]), save=False)
    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["_report"] = report
    at.run()
    assert not at.exception
    blob = " ".join(str(getattr(i, "value", "")) for i in at.info)
    assert "No vote:" in blob and "no peer group" in blob
    heads = [str(getattr(h, "value", "")) for h in at.subheader]
    assert "Valuation band" in heads and "Absolute readings" in heads      # they survive


# =========================================================================== #
# AGREEMENT-COUNT-1 - the denominator is the lenses that VOTED
# =========================================================================== #
def _ranked(label, verdict, position=14, of=14, **kw):
    return LensVote(label.lower(), label, status="ranked", verdict=verdict, position=position,
                    cohort_size=of, **kw)


def _not_applying(label):
    return LensVote(label.lower(), label, status="excluded",
                    reason="return on invested capital 10.5%; the rule requires at least 12%.")


def test_one_vote_and_two_lenses_that_did_not_apply_is_0_of_1_not_0_of_3():
    """AZN.L: Magic Formula RAW SELL 14th of 14; Value + Momentum and Growth both 'does not apply'.
    It printed 'BUY on 0 of the 3 voting lenses (2 do not apply)' and the summary said 'three voting
    tests' and 'three tests ran' before listing four."""
    forensic = LensVote("f", "Forensic", kind="check", status="ranked", verdict="buy", position=3,
                        cohort_size=14)
    ag = build_agreement([_ranked("Magic Formula RAW", "sell"), _not_applying("Value + Momentum"),
                          _not_applying("Growth"), forensic])
    assert (ag.n_ticked, ag.n_voted, ag.n_not_applying, ag.n_checks) == (3, 1, 2, 1)
    assert ag.headline == "BUY on 0 of 1 vote; 2 lenses did not apply to this company"
    assert ag.table_row("AstraZeneca PLC (AZN.L)")["BUY votes"] == "0 of 1"
    assert ag.sell == ("Magic Formula RAW",)


def test_no_lens_voted_is_said_in_words_never_0_of_0():
    ag = build_agreement([_not_applying("Value + Momentum"), _not_applying("Growth")])
    assert ag.n_voted == 0 and ag.n_not_applying == 2
    assert ag.headline == "No lens voted: every ticked lens's rules exclude this company"
    assert "0 of 0" not in ag.headline
    assert ag.table_row("Company Co")["BUY votes"] == "no vote"


def test_three_votes_is_x_of_3_and_nothing_is_said_about_lenses_that_did_not_apply():
    ag = build_agreement([_ranked("Alpha", "buy", 2), _ranked("Beta", "buy", 3),
                          _ranked("Gamma", "hold", 8)])
    assert ag.n_voted == 3 and ag.n_not_applying == 0
    assert ag.headline == "BUY on 2 of 3 votes"
    assert ag.table_row("Company Co")["BUY votes"] == "2 of 3"
    one = build_agreement([_ranked("Alpha", "buy", 2)])
    assert one.headline == "BUY on 1 of 1 vote"


def test_the_facts_pack_carries_voted_did_not_apply_and_check_counts_separately(tmp_path):
    report = _run([RAW, SCREENED, "forensic_v1"], tmp_path=tmp_path, save=False)
    say = company_facts_pack(report)["agreement"]
    assert say["lenses_that_voted"] == 1 and say["lenses_that_did_not_apply"] == 1
    assert say["check_lenses_that_do_not_vote"] == 1
    assert "voting_lenses" not in say                       # the ambiguous lump is gone
    assert say["headline"] == report.agreement.headline
    assert [l["applies"] for l in company_facts_pack(report)["lenses"]] == [True, False, True]


def test_the_run_tab_table_counts_only_the_lenses_that_voted_on_that_name():
    """The same defect lived in the Run tab's agreement table: 'BUY votes: 2 of 3' where the third
    lens had not ranked the name at all. The denominator is now the lenses that voted on THAT name."""
    from aristos_council.pipeline import LensAgreement, LensAgreementRow, lens_agreement_table

    row = LensAgreementRow(ticker="X", display="X Corp", buy_lenses=("Alpha", "Beta"),
                           hold_lenses=(), sell_lenses=(),
                           not_ranked=(("Gamma", "return on invested capital 4%"),))
    ag = LensAgreement(voting_ids=["a", "b", "c"], voting_labels={"a": "Alpha", "b": "Beta",
                                                                   "c": "Gamma"},
                       check_ids=[], check_labels={}, rows=[row])
    _cols, rows = lens_agreement_table(ag)
    assert rows[0]["BUY votes"] == "2 of 2: Alpha, Beta (1 did not apply)"
    full = LensAgreementRow(ticker="Y", display="Y Corp", buy_lenses=("Alpha",),
                            hold_lenses=("Beta",), sell_lenses=("Gamma",))
    assert lens_agreement_table(LensAgreement(
        voting_ids=["a", "b", "c"], voting_labels={}, check_ids=[], check_labels={},
        rows=[full]))[1][0]["BUY votes"] == "1 of 3: Alpha"


# =========================================================================== #
# PEER-RANK-COLUMNS-1 - one rank column per ticked lens in the Peers table
# =========================================================================== #
def _cols(report):
    from aristos_council.peer_table import rank_columns
    return rank_columns(report)


def test_one_rank_column_per_ticked_lens_that_ran_headed_with_its_size(tmp_path):
    report = _run([RAW, SCREENED, "forensic_v1"], tmp_path=tmp_path, save=False)
    cols = _cols(report)
    assert [c.kind for c in cols] == ["rank", "rank", "mark"]
    assert cols[0].header == f"Magic Formula RAW rank (of {len(report.lens_ranks[RAW]['ranked'])})"
    assert cols[2].header == "Forensic mark (check - does not vote)"          # labelled as a mark
    assert cols[0].header.endswith(f"(of {N_PEERS + 1})")


def test_the_company_is_the_first_row_marked_and_every_rank_is_the_saved_one(tmp_path):
    from aristos_council.peer_table import peer_rows
    report = _run([RAW], tmp_path=tmp_path, save=False)
    rows = peer_rows(report.peer_group, _cols(report), report.ticker)
    assert rows[0].is_company and rows[0].marked_ticker == "CO (this company)"
    assert len(rows) == N_PEERS + 1
    saved = {e["ticker"]: e["position"] for e in report.lens_ranks[RAW]["ranked"]}
    assert rows[0].ranks[0][1] == saved["CO"] == report.votes[0].position    # the vote's own rank
    for row in rows[1:]:
        assert row.ranks[0][1] == saved[row.ticker.split(".")[0]]
    # the peers stay in USD-cap order below the company
    caps = [r.cap_usd for r in rows[1:]]
    assert caps == sorted(caps, reverse=True)


def test_a_company_the_lens_screens_out_reads_does_not_apply_not_blank_not_a_number(tmp_path):
    from aristos_council.peer_table import peer_rows
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    rows = peer_rows(report.peer_group, _cols(report), report.ticker)
    company = rows[0]
    assert company.ranks[1] == (_cols(report)[1].header, "does not apply")     # CO fails min ROIC
    assert isinstance(company.ranks[0][1], int)                                 # ...but RAW ranked it


def test_no_lens_ticked_or_no_peer_group_means_no_rank_columns(tmp_path):
    none_ticked = _run([], tmp_path=tmp_path, save=False)
    assert _cols(none_ticked) == []
    lonely = _run([RAW], tmp_path=tmp_path, store=_Store([_row("P00.US", "P00")]), save=False)
    assert _cols(lonely) == []
    assert "Rank" not in format_company_report(lonely) or "rank (of" not in format_company_report(lonely)


def test_the_text_and_html_exports_carry_the_same_columns_with_the_company_first(tmp_path):
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    headers = [c.header for c in _cols(report)]
    text, html = format_company_report(report), company_report_html(report)
    for header in headers:
        assert header in text and header in html
    assert text.index("CO (this company)") < text.index("P00.US")
    assert html.index("CO (this company)") < html.index("P00.US")
    assert "does not apply" in text.split("PEERS")[1].split("VALUATION BAND")[0]


def test_the_pages_rank_columns_are_numeric_so_they_sort_by_rank(tmp_path):
    from aristos_council.peer_table import peer_frame_records, rank_display
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    records = peer_frame_records(report.peer_group, _cols(report), report.ticker)
    raw_header, screened_header = [c.header for c in _cols(report)]
    assert records[0]["Ticker"] == "CO (this company)"
    assert all(isinstance(r[raw_header], float) for r in records)              # sortable numbers
    assert records[0][screened_header] is None                                 # no number to sort on
    assert rank_display(records[0][screened_header]) == "does not apply"       # ...and it says so
    assert rank_display(3.0) == "3" and rank_display(None) == "does not apply"


def test_a_peer_with_no_data_reads_no_data_not_does_not_apply():
    from aristos_council.peer_table import RankColumn, rank_display
    col = RankColumn("Lens rank (of 2)", "rank", {"A": 1, "B": "no data", "C": "does not apply"})
    assert rank_display(col.values["B"]) == "no data"
    assert rank_display(col.values["C"]) == "does not apply"


def test_the_columns_cost_nothing_no_fetch_no_model_call(tmp_path):
    """Built from the ranks the run already saved: the day-cache counters do not move."""
    class _Counting(_Adapter):
        calls = 0

        def get_fundamentals(self, ticker):
            type(self).calls += 1
            return super().get_fundamentals(ticker)

        def get_price_history(self, ticker, *, start, end):
            type(self).calls += 1
            return super().get_price_history(ticker, start=start, end=end)
    adapter = _Counting()
    report = run_company_report("CO", [RAW, SCREENED], adapter=adapter, strategies_dir=STRAT_DIR,
                                universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                                store=_table(), save=False)
    before = _Counting.calls
    for _ in range(3):
        _cols(report)
        format_company_report(report)
        company_report_html(report)
    assert _Counting.calls == before                        # not one more fetch, with columns or not
    assert report.summary is None                            # and no model call


# =========================================================================== #
# COUNCIL-OPINION-1 — the existing council, narrator mode, one company
# =========================================================================== #
class _OpinionSpecialistRunner:
    def __init__(self):
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        from aristos_council.agents.schemas import SpecialistOutput
        from aristos_council.state import Stance
        if "SENTIMENT specialist" in system:
            return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                                    thesis="no sentiment data", agrees_with_ranker=None)
        return SpecialistOutput(stance=Stance.BULLISH, confidence=0.8,
                                thesis="ranked well against its peers", agrees_with_ranker=True)


class _OpinionDecisionRunner:
    def __init__(self, out):
        self._out = out
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        return self._out


def _opinion_runners():
    from aristos_council.agents.schemas import CriticOutput, DecisionOutput
    from aristos_council.state import Recommendation
    decision = DecisionOutput(recommendation=Recommendation.BUY, confidence=0.7,
                              rationale="It ranked well among its peers and the vote agrees.")
    return {"specialist": _OpinionSpecialistRunner(),
           "critic": _OpinionDecisionRunner(CriticOutput(counter_thesis="a counter-case")),
           "decision": _OpinionDecisionRunner(decision)}


def test_no_api_key_is_unavailable_and_reaches_no_runner(tmp_path, monkeypatch):
    from aristos_council.company_report import run_council_opinion

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = _run([RAW], tmp_path=tmp_path, save=False)
    op = run_council_opinion(report)
    assert not op.available and "ANTHROPIC_API_KEY" in op.note and op.calls == 0


def test_no_votes_is_unavailable_with_the_same_reason_the_page_shows(tmp_path):
    from aristos_council.company_report import run_council_opinion

    lonely = _Store([_row("CO.US", "CO", name="Company Co", sub="Space Tourism")])
    lonely._rows[0].industry = "Space Tourism"
    report = _run([RAW], tmp_path=tmp_path, store=lonely, save=False)
    op = run_council_opinion(report, runners=_opinion_runners())
    assert not op.available and "no peer group" in op.note


def test_an_unrateable_company_is_unavailable_with_the_data_integrity_note(tmp_path):
    from aristos_council.company_report import run_council_opinion

    class _Dead(_Adapter):
        def get_fundamentals(self, ticker):
            return (Fundamentals(ticker=ticker) if ticker == "CO"
                   else super().get_fundamentals(ticker))

        def get_price_history(self, ticker, *, start, end):
            if ticker == "CO":
                raise RuntimeError("no timezone found, symbol may be delisted")
            return super().get_price_history(ticker, start=start, end=end)

    report = run_company_report("CO", [RAW], adapter=_Dead(), strategies_dir=STRAT_DIR,
                                universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                                store=_table(), save=False)
    assert report.unrateable
    op = run_council_opinion(report, runners=_opinion_runners())
    assert not op.available and op.note.startswith("Council opinion unavailable:")


def test_a_successful_council_opinion_writes_without_voting(tmp_path):
    from aristos_council.company_report import run_council_opinion

    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    runners = _opinion_runners()
    op = run_council_opinion(report, adapter=_Adapter(), runners=runners, today=TODAY)
    assert op.available and op.narrative and "unavailable" not in op.narrative.lower()
    # SENTIMENT abstains without a call in this test env (no FINNHUB_API_KEY wired) — the
    # other three specialists (fundamental, technical, risk) each call once.
    assert runners["specialist"].calls == 3
    assert runners["critic"].calls == 1 and runners["decision"].calls == 1
    # the verdict of record is UNCHANGED by the opinion having run
    assert report.agreement.headline == build_agreement(report.votes,
                                                         band_percentile=report.check.band_percentile
                                                         ).headline


def test_run_company_report_with_council_attaches_the_opinion_and_saves_it(tmp_path):
    from aristos_council.company_report import report_record

    report = _run([RAW], tmp_path=tmp_path, save=False, with_council=True,
                  council_runners=_opinion_runners())
    assert report.council_opinion is not None and report.council_opinion.available
    record = report_record(report)
    assert record["council_opinion"]["available"] is True
    assert record["council_opinion"]["narrative"] == report.council_opinion.narrative


def test_with_council_false_by_default_touches_nothing(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.council_opinion is None


def test_council_opinion_text_and_html_sections_carry_the_narrative(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False, with_council=True,
                  council_runners=_opinion_runners())
    text = format_company_report(report)
    html = company_report_html(report)
    assert "COUNCIL OPINION" in text and report.council_opinion.narrative in text
    assert "<h2>Council opinion</h2>" in html
