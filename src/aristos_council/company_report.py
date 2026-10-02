"""COMPANY REPORT (Part B + D) - one company, measured against its own peer group.

Company Check used to be a raw diagnostic of ONE name under ONE lens against a reference universe
somebody had to pick. It now gives a verdict of its own, and the math still judges:

1. The company's PEER GROUP comes from the local market index (``market_index.peers``): the
   companies it is fairly compared with, by industry and size, widened only as far as it must.
2. For each TICKED lens, ONE ranker-only run of that lens over ``[the company] + [its peers]``,
   exactly as the Run tab ranks a list (``run_multi_strategy_pipeline``, through the day-cache). The
   company's vote under a lens is its verdict IN THAT RUN - "BUY - 3rd of 14". A lens that screens
   the company out does not vote: the row says "does not apply - <the screen's reason>", and an
   exclusion is never read as a bad rank.
3. The SAME equal-vote agreement rule the Run tab uses (SHORTLIST-3): every voting lens is a vote of
   equal weight; a check lens (Forensic) marks - clean / no concern / doubted - and does not vote;
   the valuation band marks - "priced high" - and does not veto.
4. Around that, facts about this company alone: its own valuation band (optional), its absolute
   readings and the analyst forecast table, each in the currency of its accounts.

No peer group means no lens votes: the report says why and still shows the band and the readings.

Two OPT-IN model features, both off by default, both independent of each other:

- The plain-English summary (``with_summary``), the existing reader under the READER-5 contract,
  fed a facts pack built here from the very numbers the page prints. ONE model call.
- COUNCIL-OPINION-1: the "Council opinion" (``with_council``) - the existing four-specialist +
  critic + narrator council (``graph.build_council``, unchanged), run in NARRATOR MODE on this
  one company: it WRITES about the vote above, it never issues one. "Math judges, LLM writes" -
  the equal-vote agreement stays the verdict of record whatever the council says. About six model
  calls, mostly on the cheap tier (see ``run_council_opinion``).

Neither is ticked, nothing beyond the deterministic votes is imported and nothing is called.
Every run is saved under ``runs/`` with the peer snapshot and every lens's ranks (and, when
ticked, the council opinion), so a verdict can be read later against the list it was measured on,
with no new model call on reopen.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .backtest import Badge, cohort_for_industry, format_track_record_summary, track_record
from .backtest import track_record_caption as _cohort_track_record_caption
from .company_check import (CompanyCheckResult, absolute_reading_lines, analyst_forecast_lines,
                            attach_peers, company_sources, mixed_source_marker, peers_lines,
                            run_company_check)
from .data.adapter import normalize_ticker
from .peer_table import rank_columns
from .rank_engine import MIN_RANKABLE_COHORT, too_few_to_rank_text
from .tools.valuation_band import ordinal

_ROOT = Path(__file__).resolve().parents[2]

HOUSE_LINE = ("The math judges: each vote below is the lens's own verdict on this company ranked "
              "among its peer group, and every lens counts once. Nothing here is a recommendation.")
NO_LENS_REASON = "No lens is ticked, so there is nothing to vote."
SUMMARY_NOT_ASKED = ""          # an unticked summary leaves NO section, not a placeholder
NO_KEY_NOTE_OPINION = "Council opinion unavailable: no ANTHROPIC_API_KEY set"


# --------------------------------------------------------------------------- #
# Votes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LensVote:
    """One lens's outcome for the company."""

    strategy_id: str
    label: str
    kind: str = "selector"            # "selector" votes; "check" marks
    asks: str = ""
    status: str = "absent"            # ranked | excluded | unrateable | fetch_error | absent | no_group
    verdict: str = ""                 # buy / hold / sell when ranked
    position: Optional[int] = None
    cohort_size: int = 0
    reason: str = ""                  # why it does not apply (plain words)
    factor_note: str = ""             # "ranked on 2 of 3 factors"
    # BACKTEST-2 — this lens's plain-English track record in the company's backtested cohort.
    # None when no cohort could be matched to the company's industry (never fed back into the
    # vote itself — attached AFTER votes are built, purely display).
    badge: Optional[Badge] = None

    @property
    def votes(self) -> bool:
        return (self.kind or "selector") != "check"

    @property
    def badge_suffix(self) -> str:
        """" (proven here)" beside the badge's label, or "" when there is none to show."""
        return f" ({self.badge.label})" if self.badge is not None else ""

    @property
    def ranked(self) -> bool:
        return self.status == "ranked"

    @property
    def word(self) -> str:
        """The verdict in the lens's OWN words: BUY / HOLD / SELL, or clean / no concern / doubted
        for a check."""
        from .report_language import verdict_word
        return verdict_word(self.verdict, check=not self.votes)

    def result(self) -> str:
        """"BUY - 3rd of 14", or the reason the lens does not apply."""
        if self.status == "ranked":
            where = (f"{ordinal(self.position)} of {self.cohort_size}" if self.position
                     else f"ranked of {self.cohort_size}")
            return f"{self.word} - {where}{self.factor_note}"
        if self.status == "too_few":
            # NOVOTE-1 item 2.3b — a verdict over fewer than MIN_RANKABLE_COHORT names is
            # arithmetic, not a comparison (the HLB case: "1 of 1" instead of an honest
            # abstention). Never a verdict word here, even "hold" — there is nothing to
            # compare it against. BATCH-14: the wording itself now lives in rank_engine,
            # shared with the Run tab's own guard, so it can never drift between surfaces.
            return too_few_to_rank_text(self.cohort_size)
        if self.status == "excluded":
            return f"does not apply - {self.reason}"
        if self.status == "unrateable":
            return "no data - the lens could not read this company"
        if self.status == "fetch_error":
            return "fetch failed - run it again"
        if self.status == "no_group":
            return f"not run - {self.reason}"
        return "not reported by this lens"

    @property
    def role(self) -> str:
        return "votes" if self.votes else "marks (does not vote)"


@dataclass(frozen=True)
class CompanyAgreement:
    """The equal-vote agreement for ONE company (SHORTLIST-3's rule, read off its own votes).

    AGREEMENT-COUNT-1: the denominator is the lenses that actually VOTED - ranked the company. A lens
    whose rules exclude it did not vote and is counted apart (``not_applicable``), and a check lens
    (Forensic) never votes at all (``checks``). Counting a lens that did not apply as a voter is how
    AZN.L read "BUY on 0 of the 3 voting lenses" when one lens had voted."""

    n_ticked: int                     # voting lenses ticked (they voted, or did not apply)
    buy: tuple = ()                   # voting lens labels that rated the company BUY
    hold: tuple = ()
    sell: tuple = ()
    not_applicable: tuple = ()        # ((label, reason), ...) - not a vote either way
    checks: dict = field(default_factory=dict)      # check label -> "clean" | "no concern" | "doubted"
    marks: tuple = ()                 # every caution, each a statement the run already made

    @property
    def buy_votes(self) -> int:
        return len(self.buy)

    @property
    def n_voted(self) -> int:
        """Lenses that actually voted: ranked the company BUY, HOLD or SELL."""
        return len(self.buy) + len(self.hold) + len(self.sell)

    @property
    def n_not_applying(self) -> int:
        return len(self.not_applicable)

    @property
    def n_checks(self) -> int:
        """Check lenses ticked: they mark, they never vote."""
        return len(self.checks)

    def _did_not_apply(self) -> str:
        k = self.n_not_applying
        return f"{k} {'lens' if k == 1 else 'lenses'} did not apply to this company"

    @property
    def headline(self) -> str:
        """"BUY on 0 of 1 vote; 2 lenses did not apply to this company", or - when nothing voted -
        "No lens voted: every ticked lens's rules exclude this company". Never "0 of 0"."""
        if not self.n_ticked:
            return ("No voting lens is ticked - every lens here is a check, and a check marks "
                    "rather than votes.")
        if not self.n_voted:
            return "No lens voted: every ticked lens's rules exclude this company"
        head = f"BUY on {self.buy_votes} of {self.n_voted} vote{'' if self.n_voted == 1 else 's'}"
        return f"{head}; {self._did_not_apply()}" if self.n_not_applying else head

    def table_row(self, display: str) -> dict:
        """The one row of the agreement table, the Run tab's columns for a single company."""
        checks = "; ".join(f"{label}: {word}" for label, word in self.checks.items()) or "none ticked"
        return {"Company": display,
                "BUY votes": (f"{self.buy_votes} of {self.n_voted}" if self.n_voted else "no vote"),
                "Voted BUY": ", ".join(self.buy) or "none",
                "Checks": checks,
                "Marks": "; ".join(self.marks) or "none"}


def build_agreement(votes: list[LensVote], *,
                    band_percentile: Optional[float] = None) -> CompanyAgreement:
    """The agreement over ``votes`` - pure, so it is tested without a run."""
    from .pipeline import band_mark, check_mark
    from .report_language import verdict_word

    voting = [v for v in votes if v.votes]
    checks = [v for v in votes if not v.votes]
    ranked = [v for v in voting if v.ranked]
    marks: list[str] = []
    check_words: dict = {}
    for v in checks:
        if v.ranked:
            check_words[v.label] = verdict_word(v.verdict, check=True)
            marks.append(check_mark(v.label, v.verdict))
        else:
            check_words[v.label] = "does not apply"
    marks.append(band_mark(band_percentile))
    marks += [f"{v.label}: {v.factor_note.strip(' ·')}" for v in votes if v.ranked and v.factor_note]
    return CompanyAgreement(
        n_ticked=len(voting),
        buy=tuple(v.label for v in ranked if v.verdict == "buy"),
        hold=tuple(v.label for v in ranked if v.verdict == "hold"),
        sell=tuple(v.label for v in ranked if v.verdict == "sell"),
        not_applicable=tuple((v.label, v.reason or v.result()) for v in voting if not v.ranked),
        checks=check_words, marks=tuple(m for m in marks if m))


# --------------------------------------------------------------------------- #
# COUNCIL-OPINION-1 — the council in NARRATOR MODE on this one company
# --------------------------------------------------------------------------- #
@dataclass
class CouncilOpinion:
    """The result of ``run_council_opinion``: prose, never a verdict. ``available`` False
    means nothing was written — ``note`` says why, in a sentence fit to show on the page
    (no key, no votes, a model failure) — and NOTHING here ever changes ``CompanyReport.
    agreement``, which stays the verdict of record whatever the council wrote."""

    available: bool = False
    narrative: str = ""               # markdown, via pipeline._narrative_text
    note: str = ""                    # "" when available; the reason otherwise
    calls: int = 0
    cost: Optional[float] = None      # None: not measured (a fake runner); never a silent 0
    seconds: float = 0.0


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #
@dataclass
class CompanyReport:
    ticker: str
    check: CompanyCheckResult                         # the company's own readings
    lens_ids: list = field(default_factory=list)
    votes: list = field(default_factory=list)         # LensVote, in the order the lenses were ticked
    agreement: Optional[CompanyAgreement] = None
    no_vote_reason: str = ""                          # why there is no vote, when there is none
    summary: object = None                            # a reader.ReaderResult, only when asked for
    council_opinion: Optional[CouncilOpinion] = None  # COUNCIL-OPINION-1, only when asked for
    universe: list = field(default_factory=list)      # the tickers every lens ranked, company first
    lens_ranks: dict = field(default_factory=dict)    # strategy id -> the run's ranks, for the record
    run_at: str = ""
    seconds: float = 0.0
    cache: dict = field(default_factory=dict)         # {"hits": n, "misses": n}
    saved_to: str = ""
    # BACKTEST-2 — the backtested cohort this company's industry matched (None -> no match, so no
    # badge is shown anywhere) and its "Track record from the <name> cohort, N years to <Mon
    # YYYY>" caption ("" when cohort_slug is None).
    cohort_slug: Optional[str] = None
    track_record_caption: str = ""

    @property
    def display(self) -> str:
        return self.check.display

    @property
    def peer_group(self):
        return self.check.peer_group

    @property
    def unrateable(self) -> bool:
        return bool(self.check.unrateable)

    @property
    def track_record_summary(self) -> str:
        """"Track record: 2 proven, 1 promising, 2 no edge shown" over every TICKED lens's badge
        (BACKTEST-2) - "" when none was matched to a cohort."""
        return format_track_record_summary(v.badge for v in self.votes if v.badge is not None)


# --------------------------------------------------------------------------- #
# COUNCIL-OPINION-1 — narration-only inputs, built from what the page already shows
# --------------------------------------------------------------------------- #
def _company_check_frame(report: CompanyReport):
    """A SCREEN-LESS council frame carrying Company Check's OWN identity (the same
    NARR-FRAME-1 mechanism ``pipeline._multi_lens_frame`` uses for a multi-lens Run-tab
    narration): no criteria, so the council is not framed by any ONE lens's philosophy —
    every lens's own verdict rides in as evidence instead, attributed."""
    from .strategy.loader import Strategy

    labels = ", ".join(v.label for v in report.votes)
    return Strategy.model_construct(
        id="company_check_run", name=f"Company Check: {report.display}", version=1,
        criteria=[],
        description=(f"One company ({report.display}) measured against its peer group by "
                     f"{len(report.votes)} lens(es): {labels}."),
        rationale=("Each lens reaches its own verdict on its own terms; the equal-vote "
                   "agreement is the verdict of record. This narration ATTRIBUTES what "
                   "each lens found about this company — it never reconciles the lenses "
                   "against each other or issues a net view of its own."),
        notes="", lens_kind="", lens_factor_labels=[])


def _lead_vote(votes: list[LensVote]) -> Optional[LensVote]:
    """The vote anchoring the narration's deterministic scaffolding (cohort size) — the
    first BUY, else the first ranked vote, else None (mirrors ``pipeline._lead_row``).
    Every lens's own verdict still rides in the cross-lens evidence regardless of which
    one is picked here."""
    buys = [v for v in votes if v.ranked and v.verdict == "buy"]
    if buys:
        return buys[0]
    ranked = [v for v in votes if v.ranked]
    return ranked[0] if ranked else None


def _council_agreement_row(agreement: Optional[CompanyAgreement]) -> dict:
    """The same shape ``pipeline.agreement_row_for`` builds for a multi-lens Run-tab
    narration, read off Company Check's OWN agreement instead of a lens-agreement table
    row — so the narrator opens on what the run concluded, in the SAME words the page
    shows (CHECK-WORDS-1: a check's reading, never its verdict word)."""
    if agreement is None:
        return {}
    return {"buy_votes": agreement.buy_votes, "n_voting": agreement.n_voted,
           "buy_lenses": list(agreement.buy), "sell_lenses": list(agreement.sell),
           "checks": [{"lens": label, "reading": word}
                      for label, word in agreement.checks.items()],
           "marks": list(agreement.marks)}


def _council_cross_lens_verdicts(votes: list[LensVote]) -> list[dict]:
    """EVERY ticked lens's verdict for this company, including one that did not apply —
    the same shape ``pipeline.cross_lens_verdicts`` builds, so the narrator's existing
    cross-lens checks (``narration_check.check_cross_lens``) work unchanged."""
    return [{"lens": v.label, "lens_id": v.strategy_id,
            "lens_label": f"{v.label} ({v.strategy_id})", "cell": v.result(),
            "status": v.status, "verdict": v.verdict} for v in votes]


def _council_company_facts(report: CompanyReport) -> dict:
    """COUNCIL-OPINION-2 items 1a/2.3/2.6 — the facts the Company Check page already shows
    that the council's own fresh ``gather`` call never surfaced. Never raises: an absent
    reading/trend/peer-group is simply left out of the pack, not a broken run."""
    from .tools.price_context import format_money

    check = report.check
    facts: dict = {}

    lines: list[str] = []
    debt, growth = getattr(check, "debt_and_cash", None), getattr(check, "growth_record", None)
    if debt is not None:
        lines += list(debt.lines())
    if growth is not None:
        lines += list(growth.lines())
    if lines:
        facts["absolute_readings"] = lines

    trend = getattr(check, "analyst_trend", None)
    if trend is not None and trend.available:
        facts["analyst"] = {"available": True, "lines": list(trend.lines()),
                            "source_note": trend.tag()}
    elif trend is not None:
        facts["analyst"] = {"available": False, "lines": [], "source_note": trend.headline}

    group = report.check.peer_group
    subject = getattr(group, "subject", None) if group is not None else None
    if subject is not None:
        facts["market_cap"] = {
            "local": format_money(subject.market_cap, getattr(subject, "currency", "") or "",
                                  abbreviate=True),
            "usd": format_money(subject.market_cap_usd, "USD", abbreviate=True),
            "as_of": getattr(group, "snapshot", "") or "",
        }
    return facts


def run_council_opinion(report: CompanyReport, *, adapter=None, runners=None,
                        today: Optional[date] = None) -> CouncilOpinion:
    """COUNCIL-OPINION-1 — the ONE other model feature this module offers (with the plain-
    English summary): the existing four specialists, the critic and the narrator
    (``graph.build_council``, unchanged — no second council), run in NARRATOR MODE on this
    one company. "Math judges, LLM writes": ``report.agreement`` stays the verdict of
    record whatever gets written here.

    Fed the SAME facts the page already shows — every lens's verdict and rank position
    (``cross_lens_verdicts``), the agreement row (votes, each check's own reading, the
    marks — Forensic, "priced high"), via the same fields a multi-lens Run-tab narration
    uses. The specialists still GATHER the company's own fundamentals/technical/sentiment
    through the unchanged ``gather`` node, which is where the absolute readings the
    narrator can discuss come from. Never raises: no votes, no key, or a failed call each
    return an unavailable opinion with a plain reason instead."""
    if report.unrateable:
        return CouncilOpinion(
            note=f"Council opinion unavailable: {report.check.data_integrity.note}")
    if not report.votes or report.agreement is None:
        return CouncilOpinion(note="Council opinion unavailable: "
                              f"{report.no_vote_reason or 'no vote to narrate'}")
    lead = _lead_vote(report.votes)
    if lead is None:
        # NOVOTE-1 item 2.2 — every ticked lens EXCLUDED this name (the VKTX case: a
        # pre-revenue biotech, excluded by every dividend-style lens it was checked
        # against). "Council opinion unavailable: no lens ranked this company" read as an
        # error message about a broken run; the council has nothing wrong with it; it
        # simply has nothing to narrate, because it only comments on a vote and there
        # isn't one. Specialist-only factual context (cash, price trend, analysts) with
        # no verdict is a materially new council mode — deferred, not "cheap" to add
        # alongside this wording fix; see the done-report.
        return CouncilOpinion(note="No lens voted, so there is no verdict to narrate; the "
                              "council only comments on votes.")

    import os
    if runners is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return CouncilOpinion(note=NO_KEY_NOTE_OPINION)

    started = time.perf_counter()
    today = today or date.today()
    if runners is None:
        from .agents.runners import production_runners
        runners = production_runners()
    if adapter is None:
        adapter = _default_adapter(today)

    from .graph import build_council
    from .pipeline import CouncilOutcome, _annotate_cross_lens, _cost_mark, _cost_meta, \
        _narrative_text, _sentiment_wiring
    from .persistence.reports import report_from_state
    from .state import Recommendation, ResearchState

    sentiment_adapter, sentiment_missing_key, sentiment_error = _sentiment_wiring()
    frame = _company_check_frame(report)
    cross_lens = _council_cross_lens_verdicts(report.votes)
    agreement_row = _council_agreement_row(report.agreement)
    company_facts = _council_company_facts(report)

    meter, mark = _cost_mark(runners)
    try:
        from .data.news_fallback import fetch_eodhd_news, fetch_yfinance_news
        app = build_council(adapter, frame, runners, council_mode="narrator",
                            sentiment_adapter=sentiment_adapter,
                            sentiment_missing_key=sentiment_missing_key,
                            sentiment_error=sentiment_error, run_matrix=False,
                            news_fallback_fetchers={"eodhd_fetcher": fetch_eodhd_news,
                                                   "yfinance_fetcher": fetch_yfinance_news})
        state = ResearchState.model_validate(app.invoke(ResearchState(
            ticker=report.ticker, strategy_id=frame.id,
            ranker_verdict=Recommendation(lead.verdict), ranker_explanation=lead.result(),
            ranker_cohort_size=lead.cohort_size,
            cross_lens_verdicts=cross_lens, agreement_row=agreement_row,
            company_facts_block=company_facts)))
    except Exception as exc:                              # noqa: BLE001 - reported, never raised
        cost = _cost_meta(meter, mark)
        return CouncilOpinion(
            note=f"Council opinion unavailable: {type(exc).__name__}: {exc}",
            calls=cost["actual_calls"], cost=cost["actual_cost"],
            seconds=round(time.perf_counter() - started, 1))

    rep = report_from_state(state)
    # NARR-UNION-1's cross-lens check, unchanged: flags a sentence that RECONCILES two
    # lenses' verdicts instead of just reporting them (never rewrites the prose).
    _annotate_cross_lens(rep, cross_lens)
    outcome = CouncilOutcome(ticker=report.ticker, ranker_verdict=lead.verdict,
                             council_verdict=None, agreement=None,
                             dissent_notes=rep.dissent_notes, report=rep)
    cost = _cost_meta(meter, mark)
    return CouncilOpinion(available=True, narrative=_narrative_text(outcome),
                          calls=cost["actual_calls"], cost=cost["actual_cost"],
                          seconds=round(time.perf_counter() - started, 1))


def peers_for_ranking(group) -> tuple[list[str], list[str]]:
    """``(yahoo tickers, skipped)`` for a peer group: what the ranker resolves, in the group's own
    (ticker) order. A peer with no Yahoo symbol is named, never dropped silently."""
    from .cohorts.symbols import SymbolError, yahoo_symbol

    out, skipped = [], []
    for member in group.members:
        symbol = (member.yahoo_ticker or "").strip()
        if not symbol:
            try:
                symbol = yahoo_symbol(member.ticker)
            except SymbolError:
                skipped.append(member.ticker)
                continue
        if symbol not in out:
            out.append(symbol)
    return out, skipped


def _us_line(ticker: str, store) -> Optional[str]:
    """The company's US line, for the ratings EODHD publishes only for US listings - or None."""
    from .market_index import IndexStore, load_config, us_line_of
    try:
        return us_line_of(ticker, store=store or IndexStore(load_config()["root"]))
    except Exception:                                     # no index -> no fallback, not a failure
        return None


def _default_adapter(today: date):
    from .data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from .data.provider import select_market_adapter
    return CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR, today=today)


def votes_from_multi(multi, ticker: str) -> list[LensVote]:
    """The company's outcome under every lens of a ``MultiStrategyResult``."""
    target = ticker.upper()
    row = next((r for r in multi.rows if r.ticker.upper() == target), None)
    votes = []
    for sid in multi.strategy_ids:
        result = multi.results[sid]
        strategy = getattr(result, "rank_strategy", None)
        base = dict(strategy_id=sid, label=multi.strategy_names.get(sid, "") or sid,
                    kind=getattr(strategy, "kind", "selector") or "selector",
                    asks=(getattr(strategy, "asks", "") or "").strip())
        cell = row.cells.get(sid) if row is not None else None
        if cell is None or cell.status == "absent":
            votes.append(LensVote(**base, status="absent"))
        elif cell.status == "ranked" and cell.cohort_size < MIN_RANKABLE_COHORT:
            # NOVOTE-1 item 2.3b — the HLB case: Cyclical Income ranked it "1 of 1"
            # because a classification leak (item 2.3a) left it the only name in its own
            # cohort. A rank over this few names is arithmetic, not a comparison — never
            # reported as a verdict, however the cut's own math would have scored it.
            votes.append(LensVote(**base, status="too_few", cohort_size=cell.cohort_size))
        elif cell.status == "ranked":
            votes.append(LensVote(**base, status="ranked", verdict=cell.verdict,
                                  position=cell.position, cohort_size=cell.cohort_size,
                                  factor_note=cell.factor_note))
        else:
            reason = cell.reason_plain or _plain_reason(cell.reason)
            votes.append(LensVote(**base, status=cell.status, reason=reason))
    return votes


def _plain_reason(reason: str) -> str:
    """The raw exclusion reason without the price-divergence badge some carry."""
    return (reason or "").split(" ⚠")[0].strip()


def attach_track_record(report: CompanyReport) -> None:
    """BACKTEST-2 — decorate every vote with its lens's plain-English track-record badge in the
    company's backtested cohort, and set the report's cohort slug + caption. Runs AFTER the votes
    are built and never changes one: a badge is read, never fed back in.

    The cohort is found from the company's OWN industry label (the peer group's subject), matched
    against ``data/cohort_definitions.yaml`` the same way the cohort builder matches members —
    NOT from the peer group itself, which is a different, wider ladder (COMPANY-CHECK builds a
    peer group; this maps to one of the 13 BACKTESTED cohorts, or none). No match -> every vote
    keeps ``badge=None`` and the caption stays ""; nothing else changes."""
    subject = report.peer_group.subject if report.peer_group is not None else None
    slug = cohort_for_industry(getattr(subject, "industry", None),
                               getattr(subject, "gics_subindustry", None)) if subject else None
    report.cohort_slug = slug
    if slug is None:
        return
    report.track_record_caption = _cohort_track_record_caption(slug) or ""
    report.votes = [replace(v, badge=track_record(slug, v.strategy_id)) for v in report.votes]


def lens_ranks_record(multi) -> dict:
    """Every lens's ranks over the whole list, for the saved record - the peers' verdicts too, so a
    verdict on the company can be read later against the list it was measured on."""
    from .rank_engine import cohort_positions

    out = {}
    for sid in multi.strategy_ids:
        res = multi.results[sid]
        positions = cohort_positions(res.ranked)
        out[sid] = {
            "ranked": [{"ticker": r.ticker, "position": positions.get(r.ticker, (None, False))[0],
                        "verdict": r.verdict, "combined_rank": r.combined_rank}
                       for r in res.ranked if not r.excluded],
            "excluded": [{"ticker": t, "reason": why} for t, why in res.excluded],
            "unrateable": [{"ticker": t, "reason": why} for t, why in res.unrateable],
            "fetch_errors": [{"ticker": t, "reason": why} for t, why in res.fetch_errors],
        }
    return out


def run_company_report(
    ticker: str, lens_ids: list[str], *, adapter=None, strategies_dir=None, universes_dir=None,
    runs_dir=None, today: Optional[date] = None,
    with_summary: bool = False, reader_runner=None, with_council: bool = False,
    council_runners=None, store=None, save: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> CompanyReport:
    """Build the whole report. ``adapter`` is shared by the readings and every lens run, so a name
    fetched once is read from the day-cache thereafter. Nothing here calls a model unless
    ``with_summary`` and/or ``with_council`` (COUNCIL-OPINION-1) is True — the two are
    independent tick boxes."""
    say = progress or (lambda _m: None)
    started = time.perf_counter()
    today = today or date.today()
    ticker = normalize_ticker(ticker)
    # FIND-COMPANY-UNRATEABLE-1 — the market index's OWN ticker field is EODHD's form
    # ("NFLX.US", "RIO.AU"), which is what FIND-COMPANY-1/2's search box fills the ticker
    # box with (CompanyMatch.ticker's documented contract); the adapter below is Yahoo-
    # queryable ("NFLX", "RIO.AX") and 404s on the EODHD form — every peer already goes
    # through this SAME translation (peers_for_ranking, below), but the company itself
    # never did, so every find-box pick outside the handful of exchanges whose EODHD and
    # Yahoo suffixes happen to collide (worse: ".US" is NOT one of them) came back
    # UNRATEABLE. A ticker typed directly (already Yahoo-form, or on an exchange with no
    # translation entry) is left exactly as given — SymbolError is not a failure here.
    from .cohorts.symbols import SymbolError, yahoo_symbol
    try:
        ticker = yahoo_symbol(ticker)
    except SymbolError:
        pass
    strategies_dir = Path(strategies_dir) if strategies_dir else _ROOT / "strategies"
    universes_dir = Path(universes_dir) if universes_dir else _ROOT / "universes"
    runs_dir = Path(runs_dir) if runs_dir else _ROOT / "runs"
    adapter = adapter if adapter is not None else _default_adapter(today)
    ids = [s for i, s in enumerate(lens_ids or []) if s and s not in list(lens_ids)[:i]]

    say(f"Reading {ticker}…")
    from .strategy.picker import DEFAULT_ID
    check = run_company_check(
        ticker, ids[0] if ids else DEFAULT_ID, "", adapter=adapter,
        strategies_dir=strategies_dir, universes_dir=universes_dir, runs_dir=runs_dir,
        today=today, with_analyst_trend=True,
        ratings_fallback_symbol=_us_line(ticker, store))
    attach_peers(check, store=store)
    report = CompanyReport(ticker=ticker, check=check, lens_ids=ids,
                           run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # ----- the votes ---------------------------------------------------------------------- #
    group = check.peer_group
    if check.unrateable:
        report.no_vote_reason = ("no data for this company, so there is nothing to rank "
                                 f"({check.data_integrity.note})")
    elif not ids:
        report.no_vote_reason = NO_LENS_REASON
    elif group is None or not group.available:
        why = ("; ".join(group.reasons) if group is not None and group.reasons
               else check.peer_error or "no peer group could be formed")
        report.no_vote_reason = (f"no peer group, so no lens can vote: {why}. The valuation band "
                                 f"and the readings below do not need one.")
    else:
        peers_, skipped = peers_for_ranking(group)
        universe = [ticker] + [p for p in peers_ if p.upper() != ticker.upper()]
        report.universe = universe
        say(f"Ranking {ticker} against {len(universe) - 1} peers under {len(ids)} lens"
            f"{'es' if len(ids) != 1 else ''}…")
        from .pipeline import run_multi_strategy_pipeline
        multi = run_multi_strategy_pipeline(
            universe, ids, strategies_dir=strategies_dir, universes_dir=universes_dir,
            adapter=adapter, today=today, freeze_dir=runs_dir, with_valuation_band=False,
            progress=say)
        report.votes = votes_from_multi(multi, ticker)
        report.lens_ranks = lens_ranks_record(multi)
        if skipped:
            report.check.peer_group.reasons.append(
                f"{len(skipped)} peer(s) have no Yahoo symbol and were not ranked: "
                + ", ".join(skipped))
    if not report.votes and ids:
        report.votes = [LensVote(strategy_id=sid, label=sid, status="no_group",
                                 reason=report.no_vote_reason) for sid in ids]
    # No agreement when no lens ran (no peer group, no data): "no vote, and here is why" is the
    # honest statement, not "none of the lenses applies".
    if report.votes and any(v.status != "no_group" for v in report.votes):
        report.agreement = build_agreement(report.votes, band_percentile=check.band_percentile)

    # BACKTEST-2 — badges are display only and never feed the agreement built above.
    if report.votes:
        attach_track_record(report)

    # ----- the two opt-in model features, each only when asked for -------------------------- #
    if with_council:
        say("Convening the council (narrator mode — it writes, it does not vote)…")
        report.council_opinion = run_council_opinion(report, adapter=adapter,
                                                      runners=council_runners, today=today)
    if with_summary:
        say("Writing the plain-English summary…")
        report.summary = write_company_summary(report, runner=reader_runner)

    report.seconds = round(time.perf_counter() - started, 1)
    report.cache = {"hits": getattr(adapter, "hits", None), "misses": getattr(adapter, "misses", None)}
    if save:
        try:
            report.saved_to = str(save_company_report(report, runs_dir))
        except OSError as exc:                           # a full disk must not lose the report
            report.saved_to = ""
            say(f"Could not save the run under runs/: {exc}")
    return report


# --------------------------------------------------------------------------- #
# The saved record
# --------------------------------------------------------------------------- #
def report_record(report: CompanyReport) -> dict:
    from .market_index import peer_snapshot

    group = report.peer_group
    agreement = report.agreement
    return {
        "kind": "company_report", "ticker": report.ticker, "company": report.display,
        "run_at": report.run_at, "lenses": list(report.lens_ids),
        "peer_snapshot": peer_snapshot(group) if group is not None and group.subject else None,
        "peer_sentence": group.sentence() if group is not None else "",
        "universe": list(report.universe),
        "votes": [{"lens": v.strategy_id, "label": v.label, "votes": v.votes, "status": v.status,
                   "verdict": v.verdict, "position": v.position, "of": v.cohort_size,
                   "result": v.result(),
                   # BACKTEST-2 — display only; None when no cohort was matched.
                   "track_record_badge": (None if v.badge is None else {
                       "label": v.badge.label, "verdict": v.badge.verdict,
                       "mean_excess": v.badge.mean_excess, "luck_pct": v.badge.luck_pct,
                       "years_positive": v.badge.years_positive,
                       "years_measured": v.badge.years_measured,
                       "rounds_held": v.badge.rounds_held, "note": v.badge.note})}
                  for v in report.votes],
        "agreement": (None if agreement is None else {
            "headline": agreement.headline, "buy": list(agreement.buy),
            "hold": list(agreement.hold), "sell": list(agreement.sell),
            "checks": dict(agreement.checks), "marks": list(agreement.marks)}),
        "no_vote_reason": report.no_vote_reason,
        "cohort_slug": report.cohort_slug, "track_record_caption": report.track_record_caption,
        "track_record_summary": report.track_record_summary,
        "lens_ranks": report.lens_ranks,
        "sources": [{"topic": s.topic, "text": s.text} for s in company_sources(report.check)],
        "seconds": report.seconds, "cache": report.cache,
        "summary_written": bool(getattr(report.summary, "available", False)),
        # COUNCIL-OPINION-1 — the full narrative, so a reload never needs a new model call.
        "council_opinion": (None if report.council_opinion is None else {
            "available": report.council_opinion.available,
            "narrative": report.council_opinion.narrative,
            "note": report.council_opinion.note, "calls": report.council_opinion.calls,
            "cost": report.council_opinion.cost, "seconds": report.council_opinion.seconds}),
    }


def save_company_report(report: CompanyReport, runs_dir) -> Path:
    """``runs/<UTC stamp>_company_check_<TICKER>/report.json`` (and ``report.txt``). Its name does not
    end in a strategy id and it carries no ``manifest.json``, so the frozen-run reader
    (``company_check._latest_reference_run``) never mistakes it for a replayable run."""
    stamp = report.run_at.replace(":", "-").replace("+00-00", "Z") or datetime.now(
        timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in report.ticker)
    directory = Path(runs_dir) / f"{stamp}_company_check_{safe}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(report_record(report), indent=1, ensure_ascii=False, default=str),
        encoding="utf-8")
    (directory / "report.txt").write_text(format_company_report(report), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- #
# The text export (the page and the HTML follow the SAME order)
# --------------------------------------------------------------------------- #
# summary -> council opinion -> agreement (headline + table) -> each lens's vote -> peers ->
# valuation band -> absolute readings -> analyst forecasts -> sources
SECTION_ORDER = ("summary", "council opinion", "agreement", "lens votes", "peers",
                 "valuation band", "absolute readings", "what analysts say", "sources")


def agreement_table_lines(report: CompanyReport) -> list[str]:
    row = report.agreement.table_row(report.display)
    widths = {k: max(len(k), len(str(v))) for k, v in row.items()}
    head = "  ".join(k.ljust(widths[k]) for k in row)
    body = "  ".join(str(v).ljust(widths[k]) for k, v in row.items())
    return [head, body]


def vote_table_lines(report: CompanyReport) -> list[str]:
    width = max((len(v.label) for v in report.votes), default=4)
    out = [f"{'Lens'.ljust(width)}  {'Role':<22} Result"]
    out += [f"{v.label.ljust(width)}  {v.role:<22} {v.result()}{v.badge_suffix}"
           for v in report.votes]
    return out


def summary_lines(report: CompanyReport) -> list[str]:
    """The five paragraphs, or the one-line note when it was asked for and could not be written."""
    from .reader import READER_SECTION_NOTE, reader_paragraphs

    result = report.summary
    if result is None:
        return []
    out = [f"  ({READER_SECTION_NOTE})"]
    if getattr(result, "available", False):
        for lead, text in reader_paragraphs(result.summary):
            out.append(f"  {lead} {text}")
    else:
        out.append(f"  {result.note}")
    return out


def council_opinion_lines(report: CompanyReport) -> list[str]:
    """The council's narration, or the one-line reason it is unavailable."""
    op = report.council_opinion
    if op is None:
        return []
    if not op.available:
        return [f"  {op.note}"]
    return [f"  {ln}" for ln in op.narrative.splitlines()] or ["  (no narrative produced)"]


def format_company_report(report: CompanyReport) -> str:
    """The report as text, in the page order."""
    c = report.check
    lines = [f"Company Report - {report.display}", HOUSE_LINE, ""]
    if report.unrateable:
        lines += [f"UNRATEABLE - {c.data_integrity.note}. No data, so no votes and no readings.",
                  c.pointer]
        return "\n".join(lines)

    if report.summary is not None:
        lines += ["SUMMARY", *summary_lines(report), ""]

    if report.council_opinion is not None:
        lines += ["COUNCIL OPINION (narration only — never a vote; the agreement below is "
                  "the verdict of record)", *council_opinion_lines(report), ""]

    lines.append("AGREEMENT")
    if report.agreement is not None:
        lines.append(f"  {report.agreement.headline}")
        lines.extend(f"  {ln}" for ln in agreement_table_lines(report))
        # BACKTEST-2 — display only, right under the agreement count; counts the ticked lenses'
        # badges, never the vote itself.
        if report.track_record_caption:
            lines.append(f"  {report.track_record_caption}")
        if report.track_record_summary:
            lines.append(f"  {report.track_record_summary}")
    else:
        lines.append(f"  No vote: {report.no_vote_reason}")
    lines.append("")

    lines.append("LENS VOTES")
    if report.votes:
        lines.extend(f"  {ln}" for ln in vote_table_lines(report))
        for v in report.votes:
            if v.asks:
                lines.append(f"    {v.label}: {v.asks}")
            if v.badge is not None:
                from .backtest import BADGE_MEANINGS
                lines.append(f"    {v.label} track record: {v.badge.detail_line()} — "
                             f"{BADGE_MEANINGS[v.badge.label]}")
    else:
        lines.append(f"  {report.no_vote_reason or NO_LENS_REASON}")
    lines.append("")

    peer_block = peers_lines(c, columns=rank_columns(report), company_ticker=report.ticker)
    lines.extend(peer_block if peer_block else ["PEERS: none computed"])
    lines.append("")

    lines.append("VALUATION BAND (this company against its own history; a mark, never a veto)")
    lines.append(f"  {c.valuation_band}")
    lines.append("")

    lines.append("ABSOLUTE READINGS (no comparison group; they do not vote)")
    body = absolute_reading_lines(c)
    lines.extend(body if body else ["  none available"])
    lines.append("")

    forecasts = analyst_forecast_lines(c)
    marker = (mixed_source_marker(c, c.analyst_trend.source)
              if getattr(c, "analyst_trend", None) is not None else "")
    lines.append("WHAT ANALYSTS SAY (a mark: it does not vote and changes no verdict)" + marker)
    if forecasts:
        lines.extend(forecasts[1:])                       # the heading above replaces the sub-heading
    else:
        lines.append("  not available")
    lines.append("")

    lines.append("SOURCES")
    lines.extend(f"  {s.topic}: {s.text}" for s in company_sources(c))
    tail = f"Ran in {report.seconds:.1f}s"
    if report.cache.get("hits") is not None:
        tail += f"; day-cache {report.cache['hits']} hits, {report.cache['misses']} fetched"
    lines += ["", tail + (f"; saved under {report.saved_to}" if report.saved_to else ".")]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The plain-English summary (opt-in; the only place a model is reached)
# --------------------------------------------------------------------------- #
COMPANY_PROMPT_VERSION = "reader_company_v1"
_COMPANY_PROMPT = Path(__file__).resolve().parent / "agents" / "prompts" / f"{COMPANY_PROMPT_VERSION}.md"


def company_facts_pack(report: CompanyReport) -> dict:
    """Everything the summary writer may know, built from the numbers the page prints: the
    agreement row, each lens's vote and rank, the peer group and its step, the absolute readings,
    the analyst table and the valuation band. Nothing else, so the READER-5 checks - every number and
    every company the summary names must be in THIS pack - mean something."""
    c = report.check
    group = c.peer_group
    ag = report.agreement
    lenses = [{"name": v.label, "asks": v.asks, "kind": v.kind, "votes": v.votes,
               "role": v.role, "result": v.result(),
               "verdict": v.word if v.ranked else "", "position": v.position,
               "of": v.cohort_size or None,
               "applies": v.ranked,
               # BACKTEST-2 — the LABEL only (never the raw numbers): the summary may repeat
               # this wording, but must never turn it into a claim the lens "predicts" anything.
               "track_record_label": (v.badge.label if v.badge is not None else None)}
              for v in report.votes]
    trend = c.analyst_trend
    return {
        "company": {"name": report.display,
                    "peer_group": (
                        {"sentence": group.sentence(), "step": group.step,
                         "size": len(group.members), "broad_sector_group": group.broad}
                        if group is not None and group.available else
                        {"sentence": report.no_vote_reason or "no peer group"})},
        "lenses": lenses,
        "agreement": ({
            "available": True, "headline": ag.headline,
            # AGREEMENT-COUNT-1: three separate counts, never one "voting lenses" that lumps a lens
            # that did not apply in with the ones that voted
            "lenses_that_voted": ag.n_voted, "lenses_that_did_not_apply": ag.n_not_applying,
            "check_lenses_that_do_not_vote": ag.n_checks,
            "buy_votes": ag.buy_votes, "buy_lenses": list(ag.buy), "hold_lenses": list(ag.hold),
            "sell_lenses": list(ag.sell),
            "does_not_apply": [f"{label} ({why})" for label, why in ag.not_applicable],
            "checks": dict(ag.checks), "marks": list(ag.marks),
            # the READER-5 fourth check for this page: a lens that voted BUY must be named
            "buy_lenses_to_name": list(ag.buy),
            "top_agreement": []} if ag is not None else
            {"available": False, "reason": report.no_vote_reason}),
        "valuation_band": c.valuation_band,
        "absolute_readings": {
            "debt_and_cash": c.debt_and_cash.lines() if c.debt_and_cash is not None else [],
            "growth_record": ((c.growth_record.lines() + c.growth_record.notes())
                              if c.growth_record is not None else [])},
        "what_analysts_say": ({
            "ratings": ({"summary": trend.ratings.summary_line(),
                         "strong_buy": trend.ratings.counts[0], "buy": trend.ratings.counts[1],
                         "hold": trend.ratings.counts[2], "sell": trend.ratings.counts[3],
                         "strong_sell": trend.ratings.counts[4], "total": trend.ratings.total,
                         "average_price_target": trend.ratings.target_sentence,
                         "of_the_us_listing": not trend.ratings.own_listing}
                        if trend.ratings is not None and trend.ratings.available
                        else {"not_available": trend.ratings.note if trend.ratings else ""}),
            "forecasts": trend.forecast_sentences()}
            if trend is not None else {"not_available": "no analyst data"}),
    }


def company_prompt_text() -> str:
    return _COMPANY_PROMPT.read_text(encoding="utf-8")


def write_company_summary(report: CompanyReport, *, runner=None):
    """ONE model call (plus at most one corrective retry, as the run summary has) under the READER-5
    contract. With no runner and no key it returns a one-line note and calls nothing."""
    import os

    from .reader import NO_KEY_NOTE, ReaderResult, write_summary_from_pack

    if runner is None and os.environ.get("ANTHROPIC_API_KEY"):
        from .agents.runners import LangChainRunner
        from .agents.schemas import ReaderSummary
        from .costs import CostMeter
        runner = LangChainRunner("reader", ReaderSummary, meter=CostMeter())
    if runner is None:
        return ReaderResult(note=NO_KEY_NOTE, meta={"written": False, "reason": "no key"})
    return write_summary_from_pack(company_facts_pack(report), runner=runner,
                                   prompt=company_prompt_text(),
                                   prompt_version=COMPANY_PROMPT_VERSION)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
DEFAULT_LENSES = ("magic_formula_raw_v1", "magic_formula_momentum_v1")


def main(argv=None) -> int:
    import argparse
    import os

    from .cli_guards import force_utf8_stdout

    parser = argparse.ArgumentParser(
        description="One company against its peer group: a vote per lens, the agreement, the "
                    "readings. Deterministic; the plain-English summary and the council opinion "
                    "are each opt-in and are the only things that can call a model — neither "
                    "ever changes a vote.")
    parser.add_argument("ticker")
    parser.add_argument("--lens", action="append", dest="lenses",
                        help="a rank strategy id to tick (repeat for several); default: "
                             + ", ".join(DEFAULT_LENSES))
    parser.add_argument("--summary", action="store_true",
                        help="tick the plain-English summary (one model call; needs ANTHROPIC_API_KEY)")
    parser.add_argument("--council", action="store_true",
                        help="tick the council opinion (COUNCIL-OPINION-1) — narrator mode, "
                             "about six model calls; needs ANTHROPIC_API_KEY; never votes")
    parser.add_argument("--no-save", action="store_true", help="do not write the run under runs/")
    args = parser.parse_args(argv)
    force_utf8_stdout()
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    report = run_company_report(
        args.ticker, args.lenses or list(DEFAULT_LENSES),
        with_summary=args.summary, with_council=args.council, save=not args.no_save,
        progress=lambda m: print(m, flush=True) if os.environ.get("ARISTOS_VERBOSE") else None)
    print(format_company_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
