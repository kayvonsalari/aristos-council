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
                                            run_company_report, votes_from_multi,
                                            _council_agreement_row,
                                            _council_cross_lens_verdicts)
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


def _no_news(ticker, *, today):
    """COMPANY-FACTS-TABLE-1 (Batch 15) — ``run_company_report`` always tries a news fetch
    now (``with_price_and_cash=True``); none of this file's fixtures model news, and this
    is the one seam (``run_company_report``'s own ``news_fetcher`` param) that skips
    ``data.news_fallback.gather_news_with_fallback`` entirely, rather than monkeypatching
    that shared function — which other test MODULES exercise directly and would break if
    patched globally (SENT-FALLBACK-1's own tests)."""
    from aristos_council.data.news_fallback import NewsFetchResult
    return NewsFetchResult(items=(), source="", tried=("test fixture: no news modelled",))


def _run(lens_ids, *, tmp_path, store=None, company_ebit=400.0, **kw):
    kw.setdefault("news_fetcher", _no_news)
    return run_company_report("CO", lens_ids, adapter=_Adapter(company_ebit),
                              strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                              store=store or _table(), **kw)


# =========================================================================== #
# FIND-COMPANY-UNRATEABLE-1 — a company chosen through the find box (EODHD-form ticker,
# e.g. "NFLX.US", "RIO.AU") must be translated to the Yahoo-queryable form the adapter
# expects before it is fetched, exactly as every peer already is (peers_for_ranking). Live
# regression: picking Netflix or Rio Tinto via "Find a company" came back UNRATEABLE
# because the adapter 404'd on the untranslated EODHD ticker.
# =========================================================================== #
def test_a_company_chosen_through_the_find_box_is_not_unrateable(tmp_path):
    report = run_company_report(
        "CO.US", [RAW], adapter=_Adapter(), strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
        runs_dir=tmp_path / "runs", today=TODAY, store=_table(), save=False,
        news_fetcher=_no_news)
    assert not report.unrateable
    assert report.ticker == "CO"                      # translated to the form the adapter took
    assert report.votes and report.votes[0].ranked and report.agreement is not None


def test_the_eodhd_ticker_is_translated_before_the_adapter_ever_sees_it(tmp_path):
    """Pins the mechanism itself, not just the outcome: records exactly what string the
    adapter was called with, for a US ticker (suffix drops to nothing) and a non-US one
    (AU -> AX is a DIFFERENT string, not merely a stripped one — the bug was not "trim the
    suffix", it is "ask the adapter in the form it understands")."""
    seen: list[str] = []

    class _Recorder(_Adapter):
        def get_fundamentals(self, ticker):
            seen.append(ticker)
            return super().get_fundamentals("CO")

        def get_price_history(self, ticker, *, start, end):
            return super().get_price_history("CO")

        def get_dividend_history(self, ticker, *, start, end):
            return []

    # seen[0] is the company itself (fetched first, in run_company_check, before the peer
    # ranking pass that follows fetches every peer too).
    run_company_report("CO.US", [RAW], adapter=_Recorder(), strategies_dir=STRAT_DIR,
                       universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                       store=_table(), save=False, news_fetcher=_no_news)
    assert seen[0] == "CO"

    seen.clear()
    run_company_report("CO.AU", [RAW], adapter=_Recorder(), strategies_dir=STRAT_DIR,
                       universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
                       store=_table(), save=False, news_fetcher=_no_news)
    assert seen[0] == "CO.AX"


def test_a_ticker_typed_directly_in_an_already_queryable_form_is_left_unchanged(tmp_path):
    """No translation table entry for the exchange (or already bare/Yahoo-form, as every
    manually-typed ticker always has been) -> used exactly as given. The fix must never
    touch what already worked."""
    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.ticker == "CO"


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
               "peers": "PEERS", "price and cash": "PRICE AND CASH",
               "valuation band": "VALUATION BAND",
               "absolute readings": "ABSOLUTE READINGS", "what analysts say": "WHAT ANALYSTS SAY",
               "sources": "SOURCES"}
_HTML_HEADS = {"summary": "<h2>Summary</h2>", "council opinion": "<h2>Council opinion</h2>",
               "agreement": "<h2>Agreement</h2>",
               "lens votes": "<h2>Lens votes</h2>", "peers": "<h2>Peers</h2>",
               "price and cash": "<h2>Price and cash</h2>",
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
    # TAB-MERGE-1 part 2 commit 2 order.
    assert SECTION_ORDER == ("summary", "agreement", "lens votes", "valuation band",
                             "price and cash", "absolute readings", "what analysts say",
                             "council opinion", "peers", "sources")
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
    # TAB-MERGE-1 part 2 commit 2 order (no council opinion here — not ticked).
    assert heads[:4] == ["Summary", "Agreement", "Lens votes", "Valuation band"], heads
    assert heads[-3:] == ["What analysts say", "Peers", "Sources"], heads
    assert "Price and cash" in heads and "Absolute readings" in heads
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


# =========================================================================== #
# FORENSIC-NARR-1 — the exact EL.PA shape: a check lens, a 0-of-1 vote, Growth
# not applying. The council's headline must restate the agreement's verdict of
# record word for word, and Forensic must never be given or anchor a vote.
# =========================================================================== #
def test_the_council_facts_pack_carries_the_exact_headline_and_check_labels():
    forensic = LensVote("f", "Forensic", kind="check", status="ranked", verdict="buy",
                        position=3, cohort_size=14)
    votes = [_ranked("Magic Formula RAW", "sell"), _not_applying("Growth"), forensic]
    ag = build_agreement(votes)
    assert ag.headline == "BUY on 0 of 1 vote; 1 lens did not apply to this company"

    row = _council_agreement_row(ag)
    assert row["headline"] == ag.headline

    cross = _council_cross_lens_verdicts(votes)
    raw = next(c for c in cross if c["lens"] == "Magic Formula RAW")
    check = next(c for c in cross if c["lens"] == "Forensic")
    assert raw["verdict"] == "sell" and raw["votes"] is True        # unchanged contract
    # Forensic's internal verdict is "buy" but it is a CHECK — the evidence must never
    # say so; it reads in its own words, and "votes" says plainly that it never voted.
    assert check["verdict"] == "clean" and check["votes"] is False
    assert "buy" not in check["verdict"].lower()


def test_run_council_opinion_feeds_the_headline_and_check_labels_to_the_outcome(
        tmp_path, monkeypatch):
    """The plumbing _narrative_text's validate_narration call depends on: a run with
    this exact shape must hand CouncilOutcome the agreement's own headline string and
    Forensic's label as a check, not derive anything looser."""
    from aristos_council import company_report as cr

    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    forensic = LensVote("forensic_v1", "Forensic", kind="check", status="ranked",
                        verdict="buy", position=3, cohort_size=14)
    report.votes = [report.votes[0], forensic]
    report.agreement = build_agreement(report.votes)

    import aristos_council.pipeline as pipeline_mod

    captured = {}
    orig = pipeline_mod.CouncilOutcome

    def _spy(*a, **kw):
        captured.update(kw)
        return orig(*a, **kw)

    # run_council_opinion imports CouncilOutcome LOCALLY (`from .pipeline import
    # CouncilOutcome`) on every call, so patching the pipeline module's own attribute
    # is what a fresh local import actually picks up.
    monkeypatch.setattr(pipeline_mod, "CouncilOutcome", _spy)
    cr.run_council_opinion(report, adapter=_Adapter(), runners=_opinion_runners(),
                           today=TODAY)
    assert captured.get("verdict_of_record") == report.agreement.headline
    assert captured.get("check_lens_labels") == frozenset({"Forensic"})


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
                                store=_table(), save=False, news_fetcher=_no_news)
    before = _Counting.calls
    for _ in range(3):
        _cols(report)
        format_company_report(report)
        company_report_html(report)
    assert _Counting.calls == before                        # not one more fetch, with columns or not
    assert report.summary is None                            # and no model call


# =========================================================================== #
# BACKTEST-2 — track-record badges (display only; no vote, rank or verdict above moves)
# =========================================================================== #
def test_attach_track_record_decorates_votes_without_touching_the_agreement():
    from types import SimpleNamespace

    from aristos_council.backtest import BADGE_LABELS
    from aristos_council.company_report import CompanyReport, attach_track_record

    votes = [LensVote("magic_formula_raw_v1", "Magic Formula RAW", status="ranked",
                      verdict="buy", position=2, cohort_size=14),
             LensVote("growth_garp_v2", "Growth (GARP v2)", status="ranked",
                      verdict="hold", position=6, cohort_size=14)]
    subject = SimpleNamespace(industry="Semiconductors", gics_subindustry="Semiconductors")
    check = SimpleNamespace(peer_group=SimpleNamespace(subject=subject))
    report = CompanyReport(ticker="CO", check=check, votes=votes)
    report.agreement = build_agreement(votes)
    before = report.agreement                                # the SAME object, not a copy

    attach_track_record(report)

    assert report.cohort_slug == "tech_semiconductors"
    assert report.track_record_caption.startswith(
        "Track record from the Semiconductors cohort")
    assert all(v.badge is not None and v.badge.label in BADGE_LABELS for v in report.votes)
    assert report.agreement is before                        # untouched: still the same object
    assert report.track_record_summary.startswith("Track record: ")


def test_a_does_not_apply_vote_gets_no_badge_even_with_a_cohort_match(tmp_path):
    """TAB-MERGE-1 part 2 commit 3 — a lens's historical hit rate says nothing about a
    company that lens never ranked. SCREENED (min_roic 12%) excludes CO (its ROIC here
    is ~8%, below the floor) while RAW ranks it, in the SAME backtested cohort
    (tech_semiconductors) both lenses are badge-eligible in. Only the voted one gets one."""
    from aristos_council.backtest import BADGE_LABELS

    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    assert report.cohort_slug == "tech_semiconductors"           # the match DID happen
    raw_vote = next(v for v in report.votes if v.strategy_id == RAW)
    screened_vote = next(v for v in report.votes if v.strategy_id == SCREENED)
    assert raw_vote.ranked and raw_vote.badge is not None and raw_vote.badge.label in BADGE_LABELS
    assert not screened_vote.ranked and screened_vote.status == "excluded"
    assert screened_vote.badge is None and screened_vote.badge_suffix == ""
    # the "does not apply" row states its reason only — no badge text anywhere near it
    assert screened_vote.result().startswith("does not apply - ")

    # format_company_report's own per-lens badge DETAIL line is gated on v.badge, so a
    # non-voted lens gets no "track record:" line of its own either.
    text = format_company_report(report)
    assert f"{raw_vote.label} track record:" in text
    assert f"{screened_vote.label} track record:" not in text

    # the agreement's "Track record:" summary already filtered on badge-is-not-None
    # (CompanyReport.track_record_summary), so fixing attach_track_record alone fixes
    # the count too — it counts exactly the one voted, badged lens, not two.
    assert report.track_record_summary.startswith("Track record: 1 ")
    assert "," not in report.track_record_summary

    html = company_report_html(report)
    for doc in (text, html):
        assert f"({raw_vote.badge.label})" in doc            # RAW's badge text appears


def test_no_cohort_match_leaves_every_badge_none_and_no_caption():
    from types import SimpleNamespace

    from aristos_council.company_report import CompanyReport, attach_track_record

    votes = [LensVote("magic_formula_raw_v1", "Magic Formula RAW", status="ranked",
                      verdict="buy", position=2, cohort_size=14)]
    subject = SimpleNamespace(industry="Something Nobody Backtested", gics_subindustry="")
    check = SimpleNamespace(peer_group=SimpleNamespace(subject=subject))
    report = CompanyReport(ticker="CO", check=check, votes=votes)

    attach_track_record(report)

    assert report.cohort_slug is None and report.track_record_caption == ""
    assert all(v.badge is None and v.badge_suffix == "" for v in report.votes)
    assert report.track_record_summary == ""


def test_no_peer_group_at_all_attaches_no_badge(tmp_path):
    """attach_track_record needs a subject to read an industry off; ``peer_group`` (or its
    ``subject``) can be None, and nothing should raise."""
    from aristos_council.company_report import attach_track_record

    lonely = _Store([_row("CO.US", "CO", name="Company Co", sub="Space Tourism")])
    lonely._rows[0].industry = "Space Tourism"
    report = _run([RAW], tmp_path=tmp_path, store=lonely, save=False)
    assert report.cohort_slug is None
    attach_track_record(report)                              # idempotent; no crash on a re-call
    assert report.cohort_slug is None
    assert all(v.badge is None for v in report.votes)


def test_exports_carry_the_badge_text_and_the_cohort_used(tmp_path):
    """RAW (magic_formula_raw_v1) is one of the five backtested lenses, and the fixture's
    fabricated industry/sub-industry ("Semiconductors" / "Semiconductors") is exactly the "Tech -
    Semiconductors" cohort's own rule, so this is a REAL match against the committed backtests/,
    not a fabricated one."""
    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.cohort_slug == "tech_semiconductors"
    badge = report.votes[0].badge
    assert badge is not None

    text = format_company_report(report)
    assert "Track record from the Semiconductors cohort" in text
    assert f"({badge.label})" in text
    assert report.track_record_summary in text

    html = company_report_html(report)
    assert f"({badge.label})" in html
    assert "Track record from the Semiconductors cohort" in html

    from aristos_council.company_report import report_record
    record = report_record(report)
    assert record["cohort_slug"] == "tech_semiconductors"
    assert record["track_record_caption"].startswith("Track record from the Semiconductors")
    vote_record = next(v for v in record["votes"] if v["lens"] == RAW)
    assert vote_record["track_record_badge"]["label"] == badge.label


def test_the_agreement_count_is_unchanged_whether_or_not_badges_are_attached(tmp_path):
    """Badges never gate: the SAME agreement a badge-free run would produce, read off a report
    that DOES carry badges on every vote."""
    report = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    assert any(v.badge is not None for v in report.votes)    # badges ARE attached
    from dataclasses import replace
    stripped = [replace(v, badge=None) for v in report.votes]
    again = build_agreement(stripped, band_percentile=report.check.band_percentile)
    assert again.headline == report.agreement.headline
    assert again.buy == report.agreement.buy and again.hold == report.agreement.hold
    assert again.sell == report.agreement.sell
    assert again.n_ticked == report.agreement.n_ticked and again.n_voted == report.agreement.n_voted


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


def _multi(cohort_size, *, position=1, verdict="buy", strategy_id="cyclical_income_v1"):
    """A minimal fake MultiStrategyResult: one ticker, one strategy, cohort_size exactly
    what's handed in — enough to exercise votes_from_multi without a full peer-group run."""
    from types import SimpleNamespace
    from aristos_council.pipeline import MultiStrategyCell, MultiStrategyRow

    cell = MultiStrategyCell(strategy_id=strategy_id, status="ranked", position=position,
                             cohort_size=cohort_size, verdict=verdict)
    row = MultiStrategyRow(ticker="HLB", display="HLB Co. Ltd", cells={strategy_id: cell},
                           rank_sum=position, graded=1, comparable=True)
    strategy = SimpleNamespace(kind="selector", asks="")
    result = SimpleNamespace(rank_strategy=strategy)
    return SimpleNamespace(strategy_ids=[strategy_id], strategy_names={strategy_id: "Cyclical Income"},
                           results={strategy_id: result}, rows=[row])


def test_a_lens_that_ranked_only_one_name_reports_too_few_not_rank_1_of_1():
    """NOVOTE-1 item 2.3b — the live bug, reproduced directly: Cyclical Income ranked HLB
    Co. Ltd "1 of 1" after a classification leak (item 2.3a) left it alone in its own
    cohort. A rank over this few names is arithmetic, never a verdict."""
    votes = votes_from_multi(_multi(cohort_size=1), "HLB")
    v = votes[0]
    assert v.status == "too_few" and v.ranked is False
    assert v.result() == "too few to rank (only 1 company here, not a peer group)"
    # not counted as a vote in the agreement, but visible as why it did not vote
    agreement = build_agreement(votes)
    assert agreement.buy == () and agreement.hold == () and agreement.sell == ()
    assert agreement.not_applicable == (("Cyclical Income", v.result()),)


def test_two_rankable_names_is_still_too_few_three_is_not():
    assert votes_from_multi(_multi(cohort_size=2), "HLB")[0].status == "too_few"
    assert votes_from_multi(_multi(cohort_size=3), "HLB")[0].status == "ranked"


def test_every_ticked_lens_excluding_the_company_explains_plainly_not_as_an_error(tmp_path):
    """NOVOTE-1 item 2.2 — the VKTX case: every ticked lens excludes the company (none
    ranks it), so there is an agreement row (0 of 0 voted) but no lead vote to narrate.
    "Council opinion unavailable: no lens ranked this company" read as a broken run; the
    council has nothing to comment on because it only narrates a vote, and there isn't
    one — the new wording says that plainly instead."""
    from aristos_council.company_report import run_council_opinion

    report = _run([SCREENED], tmp_path=tmp_path, save=False)   # excluded: 6% ROIC < 12%
    assert report.votes and report.votes[0].status == "excluded"
    assert report.agreement is not None                        # ticked, just none ranked
    op = run_council_opinion(report, runners=_opinion_runners())
    assert not op.available
    assert op.note == ("No lens voted, so there is no verdict to narrate; the council "
                       "only comments on votes.")
    assert "unavailable" not in op.note.lower()                 # reads as a fact, not an error


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


def test_identical_specialist_phrasing_is_flagged_in_the_final_narrative(tmp_path):
    """COUNCIL-FIX-1(e) (Batch 15) — the Company Check council path annotates convergent
    phrasing on rep.decision.rationale, the same place every other narration check lands."""
    from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
    from aristos_council.company_report import run_council_opinion
    from aristos_council.state import Recommendation, Stance

    phrase = "falling over both windows confirms sustained weakness in the name"

    class _Runner:
        def __init__(self):
            self.calls = 0

        def invoke(self, system, user):
            self.calls += 1
            if "SENTIMENT specialist" in system:
                return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                                        thesis="no sentiment data", agrees_with_ranker=None)
            return SpecialistOutput(stance=Stance.BEARISH, confidence=0.6,
                                    thesis=f"The evidence shows {phrase} on this name.",
                                    agrees_with_ranker=False)

    decision = DecisionOutput(recommendation=Recommendation.SELL, confidence=0.6,
                              rationale="It ranked poorly among its peers.")
    runners = {"specialist": _Runner(),
              "critic": _OpinionDecisionRunner(CriticOutput(counter_thesis="a counter-case")),
              "decision": _OpinionDecisionRunner(decision)}

    report = _run([RAW], tmp_path=tmp_path, save=False, company_ebit=10.0)   # a poor SELL
    op = run_council_opinion(report, adapter=_Adapter(company_ebit=10.0), runners=runners,
                             today=TODAY)
    assert op.available
    assert "narration check" in op.narrative
    assert "convergent phrasing" in op.narrative
    assert phrase in op.narrative
    # the verdict of record is UNCHANGED by the opinion having run
    assert report.agreement.headline == build_agreement(report.votes,
                                                         band_percentile=report.check.band_percentile
                                                         ).headline


def test_council_company_facts_carries_absolute_readings_and_the_peer_table_market_cap(tmp_path):
    """COUNCIL-OPINION-2 items 2.3/2.6 — the council's own evidence pack is built from
    what the PAGE already shows (debt/cash, growth record, the peer table's own, dated
    market cap), so it never has to ask for net debt the page already states, and never
    quotes a market cap from a different day than the one beside it."""
    from aristos_council.company_report import _council_company_facts

    report = _run([RAW], tmp_path=tmp_path, save=False)
    facts = _council_company_facts(report)
    assert facts["absolute_readings"]                           # debt/cash + growth lines
    assert any("debt" in ln.lower() or "cash" in ln.lower() for ln in facts["absolute_readings"])
    assert "market_cap" in facts
    assert facts["market_cap"]["as_of"] == report.check.peer_group.snapshot
    assert facts["market_cap"]["usd"]                            # non-empty formatted string
    # no EODHD key in this test env -> analyst is present but unavailable, with a reason
    assert facts["analyst"]["available"] is False and facts["analyst"]["source_note"]


def test_council_company_facts_carries_the_valuation_band_and_forward_pe(tmp_path):
    """COUNCIL-FIX-1(a)/(d) (Batch 15) — the band (whichever side of cheap/expensive) and
    the forward P/E, so the council never characterises value while silent on the band and
    never opens an open question asking for a forward multiple already on the page."""
    from aristos_council.company_report import _council_company_facts

    report = _run([RAW], tmp_path=tmp_path, save=False)
    facts = _council_company_facts(report)
    # The fake adapter's 300-bar price history gives a real (non-abstained) band.
    assert report.check.valuation_band != "—"
    assert facts.get("valuation_band") == report.check.valuation_band
    # No analyst trend in this test env -> no consensus EPS -> forward P/E abstains, so
    # the key is correctly ABSENT (never a fabricated figure).
    assert "forward_pe" not in facts


def test_the_company_facts_block_renders_the_valuation_band_and_forward_pe():
    from aristos_council.agents.nodes import _company_facts_block
    from aristos_council.state import ResearchState

    state = ResearchState(ticker="CO", strategy_id="s", company_facts_block={
        "valuation_band": "EV/EBIT 12.0 — 40th percentile of its own 5-year range",
        "forward_pe": ["forward P/E (this year) 19.7x (€142.80 / €7.25 consensus EPS)"]})
    block = _company_facts_block(state)
    assert "Valuation band" in block and "40th percentile" in block
    assert "forward P/E (this year) 19.7x" in block


def test_the_company_facts_block_instructs_citing_the_dated_market_cap_over_fundamentals():
    from aristos_council.agents.nodes import _company_facts_block
    from aristos_council.state import ResearchState

    state = ResearchState(ticker="CO", strategy_id=RAW, company_facts_block={
        "market_cap": {"local": "A$272.4bn", "usd": "$193.9bn", "as_of": "2026-09-25"},
        "absolute_readings": ["owes $13.3bn net of cash"],
        "analyst": {"available": True, "lines": ["8 analysts: 3 strong buy"],
                   "source_note": "EODHD via RIO.US"},
    })
    block = _company_facts_block(state)
    assert "2026-09-25" in block and "A$272.4bn" in block and "$193.9bn" in block
    assert "not get_fundamentals' own market_cap" in block
    assert "owes $13.3bn net of cash" in block
    assert "EODHD via RIO.US" in block and "8 analysts: 3 strong buy" in block


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


def test_run_company_report_accepts_with_council_directly(tmp_path):
    """Batch — post-merge fixes, item 1: calls ``run_company_report`` itself (not through the
    ``_run`` test helper), with stub runners, exactly as the signature is called from app.py's
    Company Check tab and from the ``--council`` CLI flag. A merge that drops ``with_council``
    from the signature fails this loudly with a TypeError, never silently."""
    report = run_company_report(
        "CO", [RAW], adapter=_Adapter(), strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
        runs_dir=tmp_path / "runs", today=TODAY, store=_table(), save=False,
        with_council=True, council_runners=_opinion_runners(), news_fetcher=_no_news)
    assert report.council_opinion is not None and report.council_opinion.available


def test_council_opinion_text_and_html_sections_carry_the_narrative(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False, with_council=True,
                  council_runners=_opinion_runners())
    text = format_company_report(report)
    html = company_report_html(report)
    assert "COUNCIL OPINION" in text and report.council_opinion.narrative in text
    assert "<h2>Council opinion</h2>" in html


# =========================================================================== #
# BATCH-14 SMALLCAP-VIEW-1 — the opt-in peer band for a company below the $5bn lens gate.
# "Tech - Semiconductors" is a REAL, committed cohort (data/cohort_definitions.yaml: industry
# "Semiconductors" narrowed to the GICS sub-industry "Semiconductors", floor $3bn) — the same
# one test_exports_carry_the_badge_text_and_the_cohort_used already matches, so this is a real
# cohort match, not a fabricated one. The company's own $5bn lens gate is UNCHANGED; what
# changes is the UNIVERSE a smallcap company is ranked against and that run's gate OVERRIDE
# (``min_market_cap_override=0.0``), so the fake adapter's own ``market_cap=2e10`` never
# matters here — only the INDEX row's ``market_cap_usd`` (what the band filters on) does.
# =========================================================================== #
from aristos_council.company_report import OUTSIDE_TESTED_RANGE_LINE


class _SmallcapAdapter(_Adapter):
    """Same fundamentals as ``_Adapter`` (peers improve with their number); LIQUID volume —
    ``_Adapter``'s own volume=10 bars would read as illiquid under SIZE-FLOOR-1's $3m ADV
    floor, which is the wrong reason for this fixture's lenses to exclude a peer."""

    def get_price_history(self, ticker, *, start, end):
        slope = 0.05 + 0.01 * _number(ticker)
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + slope * i, adj_close=100 + slope * i, volume=100_000)
            for i in range(300)])


def _semi_row(ticker, code, *, cap_usd):
    return _row(ticker, code, name=f"{code} Corp", cap=cap_usd, sub="Semiconductors")


def _smallcap_table(n_peers=4, *, subject_cap=4e9, peer_cap=3.5e9):
    rows = [_semi_row("CO.US", "CO", cap_usd=subject_cap)]
    rows += [_semi_row(f"P{i:02d}.US", f"P{i:02d}", cap_usd=peer_cap) for i in range(n_peers)]
    return _Store(rows)


def _smallcap_run(lens_ids, *, tmp_path, n_peers=4, subject_cap=4e9, peer_cap=3.5e9,
                  include_small=True, **kw):
    kw.setdefault("news_fetcher", _no_news)
    return run_company_report(
        "CO", lens_ids, adapter=_SmallcapAdapter(), strategies_dir=STRAT_DIR,
        universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY,
        store=_smallcap_table(n_peers, subject_cap=subject_cap, peer_cap=peer_cap),
        include_small=include_small, save=False, **kw)


def test_include_small_off_by_default_changes_nothing(tmp_path):
    """Item 1b — the flag defaults off; a sub-$5bn company run without it behaves exactly as
    a normal run (gated out of every voting lens, as $4bn always was and still is)."""
    report = _smallcap_run([RAW], tmp_path=tmp_path, include_small=False)
    assert report.outside_tested_range is False
    assert report.smallcap_cohort == "" and report.smallcap_floor_usd is None
    assert all(v.status != "ranked" for v in report.votes)   # the $5bn gate still excludes it


def test_a_subcap_company_ranks_against_its_smallcap_band(tmp_path):
    """Item 1c — the core positive case: $4bn, ticked, ranked against its own cohort's
    $3bn-$5bn band instead of being gated out of every lens."""
    report = _smallcap_run([RAW], tmp_path=tmp_path)
    assert report.outside_tested_range is True
    assert report.smallcap_cohort == "Tech - Semiconductors"
    assert report.smallcap_floor_usd == 3_000_000_000
    vote = report.votes[0]
    assert vote.status == "ranked" and vote.cohort_size == 5     # CO + 4 peers
    # No track record: attach_track_record is never called for this run.
    assert vote.badge is None
    assert report.cohort_slug is None and report.track_record_caption == ""


def test_the_outside_tested_range_line_is_on_the_header_and_every_vote(tmp_path):
    report = _smallcap_run([RAW, SCREENED], tmp_path=tmp_path)
    text = format_company_report(report)
    html = company_report_html(report)
    assert text.count(OUTSIDE_TESTED_RANGE_LINE) >= 1 + len(report.votes)   # header + each vote
    assert OUTSIDE_TESTED_RANGE_LINE in html
    # the vote TABLE row (not result() itself, which stays undecorated — the caveat is added
    # at the rendering layer, same reason _council_cross_lens_verdicts's "cell" text must
    # never carry it: it is a display caveat, not part of the lens's own verdict string).
    from aristos_council.company_report import vote_table_lines
    for line in vote_table_lines(report)[1:]:
        assert OUTSIDE_TESTED_RANGE_LINE in line


def test_zero_badge_strings_in_the_html_for_a_smallcap_run(tmp_path):
    """Item 1c — 'no proven/not proven/untested badges for these companies'."""
    from aristos_council.backtest import BADGE_LABELS

    report = _smallcap_run([RAW], tmp_path=tmp_path)
    html = company_report_html(report)
    for label in BADGE_LABELS:
        assert label not in html
    assert "Track record" not in html


def test_too_few_band_members_shows_too_few_to_rank(tmp_path):
    """Item 1c's own cross-reference to the existing guard — a band under
    MIN_RANKABLE_COHORT (3) reads 'too few to rank', the SAME guard Company Check's normal
    peer path already uses, not a new rule."""
    from aristos_council.rank_engine import too_few_to_rank_text

    report = _smallcap_run([RAW], tmp_path=tmp_path, n_peers=1)      # CO + 1 peer = 2, too few
    assert report.votes[0].status == "too_few"
    assert report.votes[0].result() == too_few_to_rank_text(2)


def test_no_cohort_match_falls_back_to_a_stated_no_vote_reason(tmp_path):
    """A sub-$5bn company whose industry matches no backtested cohort at all cannot be
    banded — honest abstention, not a crash and not a silent normal run."""
    store = _Store([_semi_row("CO.US", "CO", cap_usd=2e9)])
    store._rows[0].industry = "Something Nobody Backtested"
    store._rows[0].gics_subindustry = ""
    report = run_company_report(
        "CO", [RAW], adapter=_SmallcapAdapter(), strategies_dir=STRAT_DIR,
        universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY, store=store,
        include_small=True, save=False, news_fetcher=_no_news)
    assert report.outside_tested_range is False
    assert not report.votes or report.votes[0].status == "no_group"
    assert "no small-company band" in report.no_vote_reason or "no backtested cohort" \
        in report.no_vote_reason


def test_a_company_at_or_above_5bn_ignores_include_small(tmp_path):
    """Item 1d — identical to unticked. Compared field-by-field rather than by full text,
    since the text's own 'Ran in Xs' tail is wall-clock and never equal between two runs."""
    on = _smallcap_run([RAW], tmp_path=tmp_path, subject_cap=6e9, peer_cap=3.5e9,
                       include_small=True)
    off = _smallcap_run([RAW], tmp_path=tmp_path, subject_cap=6e9, peer_cap=3.5e9,
                        include_small=False)
    assert on.outside_tested_range is False and off.outside_tested_range is False
    assert [v.result() for v in on.votes] == [v.result() for v in off.votes]
    assert on.universe == off.universe
    assert (on.agreement.headline if on.agreement else None) == \
        (off.agreement.headline if off.agreement else None)


def test_a_missing_market_cap_is_never_treated_as_smallcap(tmp_path):
    """Item 1d's own edge: an UNKNOWN cap cannot be shown to be below the gate, so
    include_small has no effect — the honest reading of 'we don't know', not a guess
    either way."""
    store = _smallcap_table()
    store._rows[0] = replace_market_cap_usd_none(store._rows[0])
    report = run_company_report(
        "CO", [RAW], adapter=_SmallcapAdapter(), strategies_dir=STRAT_DIR,
        universes_dir=UNIV_DIR, runs_dir=tmp_path / "runs", today=TODAY, store=store,
        include_small=True, save=False, news_fetcher=_no_news)
    assert report.outside_tested_range is False


def replace_market_cap_usd_none(row):
    from dataclasses import replace as _dc_replace
    return _dc_replace(row, market_cap_usd=None)


def test_include_small_cli_flag_is_wired(tmp_path, monkeypatch, capsys):
    """The --include-small flag reaches run_company_report (argparse plumbing only — the
    behavior itself is pinned above)."""
    import aristos_council.company_report as cr

    captured = {}

    def _fake(ticker, lens_ids, **kw):
        captured.update(kw)
        return _smallcap_run([RAW], tmp_path=tmp_path, include_small=kw.get("include_small", False))

    monkeypatch.setattr(cr, "run_company_report", _fake)
    monkeypatch.chdir(tmp_path)
    cr.main(["CO", "--lens", RAW, "--include-small", "--no-save"])
    assert captured.get("include_small") is True


# =========================================================================== #
# BATCH-15 COMPANY-FACTS-TABLE-1 — the "Price and cash" table, end to end.
# =========================================================================== #
def test_price_and_cash_is_populated_and_in_its_own_section(tmp_path):
    from datetime import date as _date

    from aristos_council.data.news_fallback import NewsFetchResult
    from aristos_council.data.sentiment import NewsItem

    def news(ticker, *, today):
        return NewsFetchResult(
            items=(NewsItem(published=_date(2026, 9, 24), headline="AI glasses unveiled",
                            source="EODHD"),),
            source="EODHD news", tried=())

    report = _run([RAW], tmp_path=tmp_path, save=False, news_fetcher=news)
    pac = report.check.price_and_cash
    assert pac is not None and pac.last_close.available
    assert pac.news and pac.news[0].headline == "AI glasses unveiled"

    text = format_company_report(report)
    html = company_report_html(report)
    assert "PRICE AND CASH" in text and "<h2>Price and cash</h2>" in html
    assert "2026-09-24: AI glasses unveiled" in text
    assert "2026-09-24: AI glasses unveiled" in html
    # The news source is named once, in Sources — not repeated per line.
    assert "EODHD news" in text.split("SOURCES")[1]


def test_price_and_cash_names_itself_not_requested_when_the_caller_did_not_ask(tmp_path):
    """``run_company_check`` called directly (not through ``run_company_report``) without
    ``with_price_and_cash`` — the Run tab / cohort path, which never asked for this."""
    from aristos_council.company_check import run_company_check
    from aristos_council.company_report import price_and_cash_lines

    report = run_company_check(
        "CO", RAW, "", adapter=_Adapter(), strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
        runs_dir=tmp_path / "runs", today=TODAY)
    assert report.price_and_cash is None
    assert price_and_cash_lines(report) == ["  not requested"]
