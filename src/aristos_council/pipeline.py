"""Integrated pipeline (Aristos v2) — ranker is verdict-of-record, council analyses.

STAGE 1 (deterministic, free): rank the universe on proven factors; the ranker's
quintile verdict is the VERDICT-OF-RECORD and the BUY quintile is the SHORTLIST.
STAGE 2 (LLM, cost-gated): run the council ONLY on the shortlist — the four
specialists analyse (not vote) and each states whether it SUPPORTS or CHALLENGES the
ranker; the critic attacks the ranker's BUY; the Decision agent is an INDEPENDENT
SECOND OPINION (Option B) or a NARRATOR (Option A) per ``council_mode``. STAGE 3:
report both verdicts + the ranker-vs-council AGREEMENT and the dissent notes (the
forward-looking check the trailing factors lack).

Spend control: the council runs only on the ranker's shortlist (the primary cost
lever); the ranking stage spends NO LLM. The matrix node is SKIPPED here (the ranker
supersedes it). Human judgment stays the final node — this surfaces candidates and a
second opinion, it is not an oracle.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

_log = logging.getLogger(__name__)


def _log_sentiment_status() -> None:
    """ONE startup line stating the sentiment-provider status (ITEM 5 diagnostic).

    FINDING: the rank-pipeline council does NOT wire a sentiment adapter (this entry
    never builds a FinnhubAdapter, unlike the legacy run_council), so the Sentiment
    specialist ABSTAINS on every narrator run regardless of the key — it is (c) not-wired,
    not (a) key-not-reaching-process. Diagnostic only; wiring it is a separate change."""
    if os.environ.get("FINNHUB_API_KEY"):
        _log.info("sentiment (finnhub): FINNHUB_API_KEY present, but run_rank_pipeline "
                  "does not wire a sentiment adapter into the council — Sentiment "
                  "specialist abstains (ITEM 5: not-wired, not a key/env issue)")
    else:
        _log.info("sentiment (finnhub): no FINNHUB_API_KEY — sentiment abstains")

from .factors import (
    asset_kind_display,
    compute_factor_outcomes,
    gather_factor_inputs,
    is_asset_kind_out_of_scope,
    is_payout_uncovered,
    is_sector_excluded,
    is_sector_out_of_scope,
    is_unrateable,
    price_divergence_flag,
    reversion_value_for,
    screen_evaluate,
)
from .data.adapter import display_name
from .persistence.reports import RunReport, report_from_state
from .rank_engine import (
    BOUNDARY_FLAG,
    FactorSpec,
    RankedTicker,
    boundary_tie_facts,
    boundary_tie_notes,
    cohort_positions,
    format_position_cell,
    format_verdict_cell,
    rank_universe,
    ranked_table_rows,
)
from .report_language import (
    COMPARISON_MIN,
    format_limit_clause,
    format_score_gloss,
    UNIT_CURRENCY,
    UNIT_PERCENT,
    UNIT_RATIO,
    format_signed_change,
    format_summary_line,
    format_threshold,
    format_value,
    label_with_id,
)
from .reproducibility import estimate_cost
from .state import Recommendation, ResearchState
from .universe import member_hash

# The repo strategies/ and universes/ dirs, used to resolve an id when the caller does
# not pass one (the UI/CLI pass their own). src/aristos_council/pipeline.py -> repo root.
_STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"
_UNIVERSES_DIR = Path(__file__).resolve().parents[2] / "universes"
_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"        # frozen run records (ITEM 4)


@dataclass
class CouncilOutcome:
    ticker: str
    ranker_verdict: str                  # the verdict-of-record
    council_verdict: Optional[str]       # independent second opinion (None if narrator)
    agreement: Optional[str]             # AGREE | DISAGREE (None when narrating)
    dissent_notes: list[str]             # specialist challenges to the ranker
    report: RunReport


@dataclass
class PipelineResult:
    ranked: list[RankedTicker]           # full ranked universe (verdict-of-record)
    shortlist: list[str]                 # names the council ran on
    council: list[CouncilOutcome]
    council_mode: str
    excluded: list[tuple[str, str]] = field(default_factory=list)   # (ticker, reason)


def resolve_council_screen_id(rank_strategy, explicit: Optional[str] = None,
                              *, default: str = "growth_v1") -> str:
    """The screen strategy the COUNCIL judges against. An explicit --screen-strategy
    wins; otherwise the rank strategy's declared council_screen_strategy (the
    same-philosophy lens); only then the blunt default. This is the fix for the
    100%-DISAGREE artifact — a defensive ranker is no longer judged by a GARP screen."""
    if explicit:
        return explicit
    return rank_strategy.council_screen_strategy or default


def _screenless_frame(rank_strategy):
    """A screen-less council frame carrying the RANK strategy's OWN identity, for a
    strategy that declares no lens (NARR-FRAME-1). The narrator/council is framed by the
    strategy's own name + rationale — never the default GARP lens — with NO screen criteria
    (so the evidence ledger carries no screen block, and the decision prompt drops the
    partial-pass policy line). ``model_construct`` bypasses the ``criteria`` min-length
    validator on purpose: screen-less IS zero criteria."""
    from .agents.prompts import lens_brief
    from .strategy.loader import Strategy
    base = (getattr(rank_strategy, "rationale", "") or "").strip()
    note = "This strategy screens nothing; quality enters via ranking."
    rationale = f"{base}\n{note}" if base else note
    lens = lens_brief(rank_strategy)
    return Strategy.model_construct(
        id=rank_strategy.id,
        name=(getattr(rank_strategy, "display_name", "") or rank_strategy.name),
        version=getattr(rank_strategy, "version", 1),
        display_name=getattr(rank_strategy, "display_name", ""),
        criteria=[],
        rationale=rationale,
        # NARR-PROMPT-1: carry the DERIVED lens framing (kind + ranked-factor labels) so
        # EVERY agent prompt — specialists, critic, narrator — is built from what THIS
        # lens ranks and no agent imports dividend framing. "stock" -> today's prompts.
        lens_kind=lens.kind,
        lens_factor_labels=list(lens.factor_labels))


def _rank_stage(universe, rank_strategy, adapter, *, today, prefilter_criteria=None,
                with_valuation_band=False):
    from .data.adapter import TransientFetchError

    rows: list[tuple[str, dict]] = []
    excluded: list[tuple[str, str]] = []
    sources_by_ticker: dict[str, dict[str, str]] = {}
    screen_bases: dict[str, dict[str, str]] = {}     # ticker -> {criterion: basis}
    screen_outcomes: dict[str, dict[str, dict]] = {}  # ticker -> {criterion: outcome}
    abstentions_by_ticker: dict[str, dict[str, str]] = {}   # ticker -> {criterion: note}
    names_by_ticker: dict[str, str] = {}             # ticker -> company display name (ITEM 1)
    # Display-only context objects, attached to the ranked rows below. Objects, not
    # pre-rendered strings (PRICE-2): the table and the single-line surfaces format the
    # SAME numbers, so a reformat can never become a different value.
    bands_by_ticker: dict = {}                       # ticker -> ValuationBand (VALBAND-1)
    prices_by_ticker: dict = {}                      # ticker -> PriceContext (PRICE-1)
    reversion_by_ticker: dict = {}                   # ticker -> ReversionValue (PRICE-1)
    for t in universe:
        # A TRANSIENT fetch failure (429/timeout/5xx, unrecovered after retries) is NOT
        # absent data — abort THIS name with a fetch-error status (rerun), never mislabel
        # a throttled live ticker as UNRATEABLE or silently worst-rank it (ITEM 5).
        try:
            fi = gather_factor_inputs(adapter, t, today=today,
                                      with_valuation_band=with_valuation_band)
        except TransientFetchError as exc:
            excluded.append((t, f"{FETCH_ERROR_PREFIX}: {exc}"))
            continue
        f = fi.fundamentals
        if f is not None and getattr(f, "company_name", None):
            names_by_ticker[t] = f.company_name        # display label (ITEM 1)
        # UNRATEABLE: no fundamentals AND no price history (delisted / all-404). NEVER
        # ranked, no verdict, never reaches the council — applies on EVERY path.
        if is_unrateable(fi):
            excluded.append((t, "UNRATEABLE: no data — possibly delisted"))
            continue
        # ASSET-KIND gate (ETF-1 ITEM 2): the wall between asset classes, fired BEFORE
        # market-cap/sector/screen/factor so an index tracker's look-through
        # "fundamentals" can never leak into a stock lens (quiet garbage). Confirmed-only
        # — a missing quoteType never gates; it sits AFTER UNRATEABLE so a no-data name
        # (no kind) reads as UNRATEABLE, not kind-gated.
        if f is not None and is_asset_kind_out_of_scope(
                f.quote_type, getattr(rank_strategy, "asset_kinds", []) or []):
            excluded.append((t, f"asset kind '{asset_kind_display(f.quote_type)}' "
                                "outside this strategy's scope"))
            continue
        if (rank_strategy.min_market_cap is not None and f is not None
                and f.market_cap is not None
                and f.market_cap < rank_strategy.min_market_cap):
            excluded.append((t, "below min market cap"))
            continue
        if f is not None and is_sector_excluded(f.sector, rank_strategy.exclude_sectors):
            excluded.append((t, f"sector excluded ({f.sector})"))
            continue
        # Sector INCLUSION gate (FIN-1): mirror of the exclusion gate. financials_v1
        # admits ONLY financials; a confirmed out-of-scope sector is gated (a missing
        # sector never is). Independent of exclude_sectors — a strategy sets one or none.
        if f is not None and is_sector_out_of_scope(
                f.sector, getattr(rank_strategy, "include_sectors", []) or []):
            excluded.append((t, f"sector '{f.sector}' outside this strategy's scope"))
            continue
        if f is not None and is_payout_uncovered(f.payout_ratio,
                                                 rank_strategy.max_payout_ratio):
            excluded.append((t, f"payout uncovered ({f.payout_ratio:.0%} > "
                                f"{rank_strategy.max_payout_ratio:.0%})"))
            continue
        # SCREEN-AS-PREFILTER: only RANK names that already PASS the defensive
        # definition (the council screen). One source of truth; floors enforced. Capture
        # each name's per-criterion measurement basis (payout FCF/EPS) for the report.
        if prefilter_criteria is not None:
            reason, bases, abstentions, outcomes = screen_evaluate(prefilter_criteria, fi)
            if bases:
                screen_bases[t] = bases
            if abstentions:
                abstentions_by_ticker[t] = abstentions
            if outcomes:
                # REPORT-1: what EVERY rule did for this name, kept whether the name went
                # on to rank or was excluded — the "rules applied" block tallies across it.
                screen_outcomes[t] = outcomes
            if reason is not None:
                # ITEM 2: decorate (never alter) the exclusion when a fundamental floor
                # confirmed-fails while price has run up hard — visible wherever the
                # exclusion line renders (CLI, Universe Run tab, snapshot notes).
                flag = price_divergence_flag(fi, prefilter_criteria)
                if flag:
                    reason = f"{reason} {flag}"
                excluded.append((t, reason))
                continue
        outcomes = compute_factor_outcomes(
            fi, [fac.name for fac in rank_strategy.factors])
        rows.append((t, {n: v for n, (v, _) in outcomes.items()}))
        sources_by_ticker[t] = {n: s for n, (_, s) in outcomes.items()}
        # PRICE-1 (display only): the share price + 52-week position for EVERY rateable
        # name, ALWAYS — it reads bars this run already fetched, so there is nothing to
        # gate. The reversion value rides with the band flag (it reuses the band's inputs).
        if fi.price_context is not None:
            prices_by_ticker[t] = fi.price_context
        if with_valuation_band:                           # VALBAND-1 (display only, opt-in)
            bands_by_ticker[t] = fi.valuation_band
            reversion_by_ticker[t] = reversion_value_for(fi)
    specs = [FactorSpec(fac.name, fac.direction, fac.missing)
             for fac in rank_strategy.factors]
    ranked = rank_universe(rows, specs, cut=rank_strategy.cut, k=rank_strategy.k,
                           percentile=rank_strategy.percentile,
                           missing=rank_strategy.missing)
    # Attach the per-factor SOURCE tags recorded at compute time (ITEM 1) — the report's
    # factor-integrity block reads these to disclose EV vs proxy vs abstained per name.
    for r in ranked:
        if r.ticker in sources_by_ticker:
            r.factor_sources = sources_by_ticker[r.ticker]
        if r.ticker in abstentions_by_ticker:
            r.screen_abstentions = abstentions_by_ticker[r.ticker]
        if r.ticker in bands_by_ticker:
            r.valuation_band = bands_by_ticker[r.ticker]
        if r.ticker in prices_by_ticker:
            r.price = prices_by_ticker[r.ticker]
        if r.ticker in reversion_by_ticker:
            r.reversion = reversion_by_ticker[r.ticker]
    return ranked, excluded, screen_bases, names_by_ticker, screen_outcomes


def _shortlist(ranked: list[RankedTicker], runs_on: str, k: int) -> list[RankedTicker]:
    live = [r for r in ranked if not r.excluded]
    if runs_on == "all":
        return live
    if runs_on == "top_k":
        return live[:k]                  # `ranked` is already sorted best-first
    return [r for r in live if r.verdict == "buy"]   # buy_quintile (default)


def _peer_rows(cohort: Optional[list[RankedTicker]],
               ticker: str) -> dict[str, dict]:
    """Every OTHER live name's authoritative row, keyed by ticker (NARR-CHK-FP-2).

    The narrator compares the name it writes about with its cohort ("the identically scored
    MSFT that received HOLD"), and such a claim is only checkable against the row of the name
    it NAMES — checked against the narrated name's row it read as a contradiction though it
    was true (live on the 2026-08-10 GOOGL narration). Positions come from
    ``cohort_positions`` over the LIVE cohort, the same tie-shared ordinal every ranked table
    displays, so a peer claim is judged by exactly what the reader sees. Empty when no cohort
    is supplied — the check then behaves exactly as before. Never derived from the SHORTLIST
    alone: positions taken over a subset would be the wrong ordinals, and a wrong row is
    worse than no row."""
    live = [r for r in (cohort or []) if not r.excluded]
    if not live:
        return {}
    positions = cohort_positions(live)
    out: dict[str, dict] = {}
    for r in live:
        if r.ticker == ticker:
            continue
        pos, _tied = positions.get(r.ticker, (None, False))
        out[r.ticker] = {"factors": dict(r.factor_ranks), "combined_position": pos,
                         "score": r.combined_rank, "verdict": r.verdict}
    return out


def _annotate_narration(rep: RunReport, r: RankedTicker,
                        boundary_tie: Optional[dict] = None,
                        cohort: Optional[list[RankedTicker]] = None) -> None:
    """Append rank-semantics contradiction annotations to the narrative in place (ITEM 4).

    Verifies the narrator's ordinal claims against ``r``'s authoritative rank table and
    appends `[⚠ narration check: …]` lines on a contradiction — never rewrites the prose.
    No-op when there is no rationale to check. ``boundary_tie`` (VERDICT-TIE-1) is this
    name's boundary-tie fact when its verdict split from a tie partner's on the alphabetical
    tie-break: the annotated table is authoritative, so prose that ORDERS the name against a
    partner it is TIED with ("decisively ranked below VWCE.DE") is a contradiction.

    ``score`` hands the checker the authoritative combined rank-SUM, so a cited rank-sum
    VALUE that contradicts the table ("a rank sum of 6" when the table says 6.5)
    is caught (NARR-CHK-5).

    ``combined_position`` is ``r.cohort_position`` (NARR-EVIDENCE-1) — the SAME tie-shared
    ordinal ``ranked_table_rows``/``RankedTicker.explain`` render and the narrator is
    actually handed (via ``ranker_explanation``), not the sequential ``rank_position``. The
    two diverge on every name after a tie: an honest "#1 of 3" from a name tied for the top
    spot was being checked against its sequential position (2), stamping a false
    contradiction on the opening rank line of every narration downstream of a tie — live on
    the 2026-08-04 bond and equity runs. One source of truth for the position a claim is
    checked against, matching the one every ranked-table surface already displays."""
    from .narration_check import check_narration
    d = getattr(rep, "decision", None)
    if d is None or not getattr(d, "rationale", ""):
        return
    table = {"N": r.universe_size, "combined_position": r.cohort_position,
             "factors": dict(r.factor_ranks), "ticker": r.ticker,
             "score": r.combined_rank,
             "boundary_tie": boundary_tie or {},
             # NARR-CHK-FP-2: the cohort's other rows, so a CROSS-NAME claim is judged
             # against the name it names instead of stamping honest prose.
             "peers": _peer_rows(cohort, r.ticker)}
    annotations = check_narration(d.rationale, table)
    if annotations:
        d.rationale = d.rationale.rstrip() + "\n" + "\n".join(annotations)


# The ETF lens's absolute-value factors (NARR-LEDGER-1, absorbs NARR-STATIC-2): the fee/
# size/yield numbers a reader actually wants to AUDIT against a real-world unit ("is 0.07%
# actually cheap?", "is EUR 50bn actually large?"). Every other rank factor (momentum,
# growth, quality, …) is already auditable via its rank alone, so this set stays narrow.
_ABSOLUTE_LEDGER_FACTORS = ("expense_ratio", "fund_size", "distribution_yield")


def _static_factor_evidence(r: RankedTicker) -> list[dict]:
    """Absolute values for this name's fee/size/yield factors (NARR-LEDGER-1, absorbs
    NARR-STATIC-2), as ``{factor, value, provenance}`` entries for the narrator's evidence
    ledger — WHATEVER their source. NARR-STATIC-1 plumbed only the STATIC-served subset
    (``src.startswith("static:")``); a vendor-COMPUTED fee/AUM/yield — the common case,
    since the static layer is a FALLBACK for a field the vendor already served plausibly
    (``etf_static.apply_static_fill``) — never reached the narrator at all, so the writer
    honestly reported it "not present anywhere in the ledger" for most names. Each entry now
    carries its ACTUAL provenance tag (``static: <as_of>, <source>``, ``computed``, or either
    with a currency receipt appended — see ``factors._with_fx_receipt``), exactly as the
    report's factor-integrity block discloses it — never rewritten to look more authoritative
    than it is. A factor absent from this name's rank set, or genuinely abstained/stale-
    withheld (value is None either way), is OMITTED — never a phantom fill (the null≠false
    discipline). A stock lens (none of these three factors ranked) leaves the ledger empty,
    same as before."""
    out: list[dict] = []
    for name, src in r.factor_sources.items():
        if name not in _ABSOLUTE_LEDGER_FACTORS or not isinstance(src, str):
            continue
        value = r.factor_values.get(name)
        if value is None:
            continue
        out.append({"factor": name, "value": value, "provenance": src})
    return out


def _council_stage(
    shortlist: list[RankedTicker], screen_strategy, adapter, runners, mode: str, *,
    sentiment_adapter=None, sentiment_missing_key: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    boundary_ties: Optional[dict[str, dict]] = None,
    cohort: Optional[list[RankedTicker]] = None,
) -> list[CouncilOutcome]:
    """Run the LLM council over the shortlist (matrix skipped — the ranker is the
    verdict-of-record). Shared by ``run_pipeline`` and ``run_rank_pipeline`` so the
    per-name invocation lives in ONE place. ``progress`` (optional) is called with a
    human status string before each name — the narrator phase is minutes, so the UI
    needs a heartbeat. ``boundary_ties`` (VERDICT-TIE-1) is ``boundary_tie_facts`` over the
    WHOLE ranked cohort — the shortlist alone cannot see a tie partner that fell on the
    other side of the boundary — threaded into each narrated name's evidence so the writer
    can state honestly that the verdict split on the tie-break."""
    from .graph import build_council        # local import: avoids a heavy import cycle

    app = build_council(adapter, screen_strategy, runners,
                        sentiment_adapter=sentiment_adapter,
                        sentiment_missing_key=sentiment_missing_key,
                        council_mode=mode, run_matrix=False)
    outcomes: list[CouncilOutcome] = []
    n = len(shortlist)
    for i, r in enumerate(shortlist, 1):
        if progress is not None:
            progress(f"Narrating {r.ticker} ({i} of {n})…")
        imputed_fraction = (len(r.imputed_factors) / len(r.factor_ranks)
                            if r.factor_ranks else 0.0)
        boundary_tie = (boundary_ties or {}).get(r.ticker, {})
        result = ResearchState.model_validate(app.invoke(ResearchState(
            ticker=r.ticker, strategy_id=screen_strategy.id,
            ranker_verdict=Recommendation(r.verdict),
            ranker_explanation=r.explain(),
            ranker_cohort_size=r.universe_size,
            ranker_imputed_fraction=imputed_fraction,
            ranker_boundary_tie=dict(boundary_tie),
            static_factor_evidence=_static_factor_evidence(r))))
        rep = report_from_state(result)
        # ITEM 4: rank-semantics post-check (+ VERDICT-TIE-1 tie-ordering check). The whole
        # ranked cohort rides along so a CROSS-NAME claim is checked against the row of the
        # name it names (NARR-CHK-FP-2); the shortlist alone cannot see a compared peer.
        _annotate_narration(rep, r, boundary_tie, cohort=cohort)
        outcomes.append(CouncilOutcome(
            ticker=r.ticker, ranker_verdict=r.verdict,
            council_verdict=rep.council_verdict,
            agreement=rep.ranker_council_agreement,
            dissent_notes=rep.dissent_notes, report=rep))
    return outcomes


def run_pipeline(
    *, universe: list[str], rank_strategy, screen_strategy, adapter, runners,
    today: date, sentiment_adapter=None, sentiment_missing_key: bool = False,
    council_runs_on: Optional[str] = None, council_mode: Optional[str] = None,
) -> PipelineResult:
    """Run the full ranker->council pipeline. ``council_runs_on`` / ``council_mode``
    default to the rank strategy's config; pass to override. The council uses the
    SCREEN strategy for evidence/analysis, with the matrix node skipped (the ranker
    is the deterministic verdict-of-record)."""
    runs_on = council_runs_on or rank_strategy.council_runs_on
    mode = council_mode or rank_strategy.council_mode

    # If prefilter is on, the SAME screen the council judges by also gatekeeps the
    # ranking — ranker and council share one defensive definition.
    prefilter = (screen_strategy.criteria
                 if getattr(rank_strategy, "prefilter_screen", False) else None)
    ranked, excluded, _, _, _ = _rank_stage(universe, rank_strategy, adapter, today=today,
                                          prefilter_criteria=prefilter)
    shortlist = _shortlist(ranked, runs_on, rank_strategy.k)

    outcomes = _council_stage(
        shortlist, screen_strategy, adapter, runners, mode,
        sentiment_adapter=sentiment_adapter,
        sentiment_missing_key=sentiment_missing_key,
        boundary_ties=boundary_tie_facts(ranked), cohort=ranked)

    return PipelineResult(ranked=ranked, shortlist=[r.ticker for r in shortlist],
                          council=outcomes, council_mode=mode, excluded=excluded)


# --------------------------------------------------------------------------- #
# High-level entry — the ONE callable both the CLI and the UI drive
# --------------------------------------------------------------------------- #
UNRATEABLE_PREFIX = "UNRATEABLE"
FETCH_ERROR_PREFIX = "FETCH_ERROR"     # a transient fetch failure (ITEM 5), NOT absent


@dataclass
class RankPipelineResult:
    """The full v2 run, structured for a caller to render without re-deriving.

    ``ranked`` is the LIVE ranked universe (verdict-of-record, best-first; excluded
    names removed). ``excluded`` and ``unrateable`` are split so the UNRATEABLE
    no-data names (delisted) read distinctly from screen/cap exclusions.
    ``narratives`` maps a shortlisted (BUY) name to its LLM narration markdown (empty
    in ranker-only runs). ``header`` is the division-of-labor line; ``meta`` carries
    the ids, sizes, and the cost estimate the CLI computes."""

    ranked: list[RankedTicker]
    excluded: list[tuple[str, str]]
    unrateable: list[tuple[str, str]]
    narratives: dict[str, str]
    header: str
    meta: dict
    council_mode: str = "narrator"                    # lets agreement_table/format_narratives read it
    council: list[CouncilOutcome] = field(default_factory=list)
    shortlist: list[str] = field(default_factory=list)
    # Names that hit a TRANSIENT fetch failure this run (ITEM 5) — aborted, NOT ranked
    # and NOT UNRATEABLE. A rerun should recover them; surfaced on its own axis.
    fetch_errors: list[tuple[str, str]] = field(default_factory=list)
    # Per-name screen-criterion measurement basis over ALL screened names (ranked or
    # excluded), e.g. {"PEP": {"max_payout_ratio_fcf": "fcf"}} — the payout-basis
    # disclosure counts across this.
    screen_bases: dict[str, dict[str, str]] = field(default_factory=dict)
    # Ticker -> company display name (yfinance longName), for every processed name
    # (ranked, excluded, unrateable). Render surfaces lead a line with
    # display_name(ticker, names.get(ticker)); a missing entry falls back to the bare
    # ticker (ITEM 1).
    names: dict[str, str] = field(default_factory=dict)
    # REPORT-1 — what EVERY screen rule did for EVERY screened name:
    # {ticker: {criterion: {"passed", "observed", "threshold", "note", "basis",
    # "borderline"}}}. The old report could only ever name the FIRST rule a name failed,
    # so a rule nothing failed was invisible and a reader could not tell what had been
    # applied at all. The "Rules applied" block tallies across this. Display only.
    screen_outcomes: dict[str, dict[str, dict]] = field(default_factory=dict)
    # The LOADED strategy objects this run actually used, INCLUDING any per-run
    # overrides — so the rules block is read from what RAN, never from a hardcoded list.
    # A strategy edited tomorrow changes the block automatically. None on a screen-less
    # run (no lens declared) or for a hand-built result.
    rank_strategy: object = None
    screen_strategy: object = None


def tie_boundary_notes(ranked: list[RankedTicker]) -> dict[str, str]:
    """Ticker -> the rendered boundary-tie mark for EVERY member of a tie whose verdicts
    differ, e.g. ``⚑ boundary (tied 10 with EUNL.DE — HOLD; tie broken alphabetically)``.

    VERDICT-TIE-1 supersedes the original ITEM 7 disclosure (which annotated only the LOWER
    row with ``(=20.0 — tie broken alphabetically)``): the tie-break decided BOTH sides of
    the boundary, so both sides carry the mark, and each names its tie partner(s), the shared
    score, and the differing verdict. Thin re-export of
    ``rank_engine.boundary_tie_notes`` — the detection and the wording live there, next to
    ``format_verdict_cell``, so every ranked-table surface renders the same string.

    Display-only: ordering, the alphabetical tie-break, the cut and the verdicts are all
    unchanged — tied names KEEP their individual ranker verdicts."""
    return boundary_tie_notes(ranked)


def _disp(result: "RankPipelineResult", ticker: str) -> str:
    """The leading label for a report line: 'Company Name (TICKER)' or the bare ticker
    when the name is unknown (ITEM 1)."""
    return display_name(ticker, result.names.get(ticker))


def _name_col(label: str, width: int = 34) -> str:
    """Pad/truncate a display label to keep the ranked table's trailing columns aligned."""
    return label if len(label) <= width else label[: width - 1] + "…"


def _pipeline_header(mode: str) -> str:
    if mode == "ranker-only":
        return "Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran)."
    tail = "non-judging" if mode == "narrator" else "independent second opinion"
    return f"Verdict: deterministic ranker.  Narrative: LLM ({tail})."


def _narrative_text(outcome: CouncilOutcome) -> str:
    d = outcome.report.decision
    return (d.rationale.strip() if d and d.rationale else "") or "(no narrative produced)"


def _split_exclusions(
    ranked: list[RankedTicker], prerank_excluded: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Partition every non-ranked name into (excluded, unrateable, fetch_errors).
    Sources: the pre-rank exclusions (cap/sector/payout/screen/UNRATEABLE/FETCH_ERROR)
    AND any rank-engine exclusion (a missing 'exclude'-mode factor). UNRATEABLE (a
    delisted no-data name) and FETCH_ERROR (a transient failure — rerunnable) each get
    their OWN axis, never mixed with a screen fail."""
    engine_excluded = [(r.ticker, r.reason) for r in ranked if r.excluded]
    everything = list(prerank_excluded) + engine_excluded
    fetch_errors = [(t, why) for t, why in everything
                    if why.startswith(FETCH_ERROR_PREFIX)]
    unrateable = [(t, why) for t, why in everything
                  if why.startswith(UNRATEABLE_PREFIX)]
    excluded = [(t, why) for t, why in everything
                if not why.startswith(UNRATEABLE_PREFIX)
                and not why.startswith(FETCH_ERROR_PREFIX)]
    return excluded, unrateable, fetch_errors


def _resolve_strategy_path(strategy_id: str, strategies_dir: Path) -> Path:
    return (Path(strategy_id) if strategy_id.endswith((".yaml", ".yml"))
            else strategies_dir / f"{strategy_id}.yaml")


def run_rank_pipeline(
    universe: Optional[list[str]] = None, strategy_id: str = "", *,
    universe_id: Optional[str] = None, universes_dir: str | Path | None = None,
    council_mode: str = "narrator", csv_path: str | Path | None = None,
    ranker_only: bool = False, strategies_dir: str | Path | None = None,
    screen_strategy_id: Optional[str] = None,
    council_runs_on: Optional[str] = None, narrate_coverage: str = "buys_only",
    adapter=None, runners=None, today: Optional[date] = None,
    use_cache: bool = True, progress: Optional[Callable[[str], None]] = None,
    freeze_dir: str | Path | None = None, replay_run_id: Optional[str] = None,
    with_valuation_band: bool = False,
) -> RankPipelineResult:
    """Rank a universe under a RANK strategy, then (unless ``ranker_only``) narrate
    the shortlist with the LLM council. The single entrypoint the CLI and Council
    Station both call — no subprocess, no duplicated orchestration.

    Pass EITHER an explicit ``universe`` ticker list OR a ``universe_id`` naming a
    manifest under ``universes/`` (a declared, versioned input). The resolved id is
    stamped into ``meta['universe_id']`` — a named manifest keeps its id; an ad-hoc
    list is recorded as ``adhoc:<hex8>`` so identical ad-hoc runs link.

    Deterministic STAGE 1 (screen -> rank -> gates) always runs and is free; STAGE 2
    (the council) runs only when ``ranker_only`` is False and bills API credits. When
    ``adapter``/``runners`` are None they are built from the environment
    (``ARISTOS_MARKET_PROVIDER``; ``ANTHROPIC_API_KEY`` for the council) — tests inject
    fakes instead. ``progress`` receives per-phase status strings for a live UI.

    ``narrate_coverage`` (NARR-2) controls WHICH ranked names get an LLM narrative:
    ``"buys_only"`` (default, cheapest — the strategy's usual BUY-tier shortlist) or
    ``"all"`` (every ranked live name, for core/ETF cohorts where the HOLDs are live
    candidates). Coverage only — the deterministic ranker verdicts are byte-unchanged.

    ``with_valuation_band`` (VALBAND-1, default False) adds the ABSOLUTE valuation-band
    context column for every rateable name — where today's valuation sits in that name's
    OWN 5-year distribution. Opt-in because it costs a second (5-year) price fetch per
    name; it NEVER re-grades, changes a rank, or feeds a verdict. Off -> no band
    computation, no extra fetch, and the output is byte-identical to a pre-VALBAND run."""
    strategies_dir = Path(strategies_dir) if strategies_dir else _STRATEGIES_DIR
    universe, resolved_universe_id, universe_name = _resolve_universe(
        universe, universe_id,
        Path(universes_dir) if universes_dir else _UNIVERSES_DIR)
    rank_strategy = load_rank_strategy_from_id(strategy_id, strategies_dir)
    # NARR-FRAME-1 (same root as CCFIX-2): resolve the lens WITHOUT the blunt default. A
    # strategy that declares no council_screen_strategy (and none was passed) is
    # SCREEN-LESS — the narrator/council is framed by the strategy's OWN identity, never
    # the default GARP lens, and no screen criteria enter the evidence.
    resolved_screen_id = screen_strategy_id or rank_strategy.council_screen_strategy
    screen_less = not resolved_screen_id
    screen_strategy = (None if screen_less
                       else load_screen_from_id(resolved_screen_id, strategies_dir))
    # The Strategy object that FRAMES the council: the real lens when screened, else a
    # screen-less frame carrying the rank strategy's own identity (no criteria).
    council_frame = _screenless_frame(rank_strategy) if screen_less else screen_strategy
    if not screen_less:
        # NARR-PROMPT-1: a SCREENED frame is the lens YAML, which knows nothing about the
        # rank strategy that selected it — stamp the derived lens framing onto a COPY so
        # every agent is framed by what the active rank strategy ranks (an ETF rank
        # strategy with a screen lens no longer inherits stock framing). A stock lens
        # derives to "stock" -> the frame is unchanged in effect.
        from .agents.prompts import lens_brief
        _lens = lens_brief(rank_strategy)
        council_frame = council_frame.model_copy(update={
            "lens_kind": _lens.kind, "lens_factor_labels": list(_lens.factor_labels)})

    today = today or date.today()
    mode = council_mode or rank_strategy.council_mode
    runs_on = council_runs_on or rank_strategy.council_runs_on

    # ITEM 4 — offline replay serves a FROZEN run record (no network); otherwise, if a
    # freeze_dir is set, wrap the live adapter to CAPTURE every raw payload for freezing.
    recording = None
    if replay_run_id:
        from .persistence.replay import FrozenAdapter
        base = Path(freeze_dir) if freeze_dir else _RUNS_DIR
        adapter = FrozenAdapter(base / replay_run_id)
    else:
        if adapter is None:
            adapter = _build_adapter(today=today, use_cache=use_cache)
        if freeze_dir:
            from .persistence.replay import RecordingAdapter
            recording = RecordingAdapter(adapter)
            adapter = recording

    if progress is not None:
        progress("Screening & ranking the universe…")
    prefilter = (screen_strategy.criteria
                 if (screen_strategy is not None
                     and getattr(rank_strategy, "prefilter_screen", False)) else None)
    ranked, prerank_excluded, screen_bases, names, screen_outcomes = _rank_stage(
        universe, rank_strategy, adapter, today=today, prefilter_criteria=prefilter,
        with_valuation_band=with_valuation_band)
    live = [r for r in ranked if not r.excluded]
    excluded, unrateable, fetch_errors = _split_exclusions(ranked, prerank_excluded)

    # NARR-2 ITEM 2: narration coverage. "buys_only" (default, cheapest) narrates the
    # rank strategy's usual shortlist (the BUY tier); "all" narrates every ranked
    # (live) name so an ETF/core cohort's HOLDs — live candidates, not rejects — are
    # covered too. Presentation/coverage only: the ranker verdicts are byte-unchanged.
    if narrate_coverage == "all":
        shortlist = list(live)                        # every ranked name, best-first
    else:
        shortlist = _shortlist(ranked, runs_on, rank_strategy.k)
    est = estimate_cost(len(shortlist))

    council: list[CouncilOutcome] = []
    narratives: dict[str, str] = {}
    if not ranker_only and shortlist:
        _log_sentiment_status()          # ITEM 5 diagnostic — one line, no behavior change
        # Disclose the ACTUAL post-screen shortlist cost before the narrator spends
        # (ITEM 4) — the pre-run estimate is an upper bound; this is the real number,
        # from the shortlist we already have (no second screen run).
        if progress is not None:
            progress(f"Shortlist: {len(shortlist)} name(s) → ${est:.2f} — "
                     "starting narration…")
        if runners is None:
            from .agents.runners import production_runners
            runners = production_runners()
        council = _council_stage(shortlist, council_frame, adapter, runners, mode,
                                 progress=progress,
                                 boundary_ties=boundary_tie_facts(ranked),
                                 cohort=ranked)
        narratives = {o.ticker: _narrative_text(o) for o in council}

    # Freeze the captured inputs into a run record (ITEM 4). Replay runs record which
    # run_id they reproduced. The frozen values are what make the run replayable.
    run_id = replay_run_id
    if recording is not None:
        from .persistence.replay import freeze_run, make_run_id
        run_id = make_run_id(rank_strategy.id)
        freeze_run(recording, run_id=run_id, runs_dir=freeze_dir)

    # The stamp tells the TRUTH: a ranker-only run stamps "ranker-only", not the disabled
    # selector's leaked "narrator" value (ITEM 3). No LLM ran -> say so.
    executed_mode = "ranker-only" if ranker_only else mode
    meta = {
        "rank_strategy_id": rank_strategy.id,
        # The FRIENDLY name for report headers (REPORT-HTML-1) — display-only, and empty
        # when the strategy declares none, so a renderer falls back to the id rather than
        # inventing a label. The id stays the record key everywhere.
        "rank_strategy_name": (getattr(rank_strategy, "display_name", "")
                               or getattr(rank_strategy, "name", "") or ""),
        # Screen-less strategies render "none" — never the leaked default lens (NARR-FRAME-1).
        "screen_strategy_id": screen_strategy.id if screen_strategy is not None else "none",
        # REPORT-1 — the human names beside the ids. A report leads with these and keeps
        # the id parenthetically; empty when the source declares none, so a renderer
        # falls back to the id rather than inventing a label.
        "screen_strategy_name": (getattr(screen_strategy, "display_name", "")
                                 or getattr(screen_strategy, "name", "") or ""
                                 ) if screen_strategy is not None else "",
        "universe_name": universe_name,
        # Whether the screen ran as a PREFILTER (names failing a rule were never ranked)
        # or not at all. Material: a reader must know whether a failing name was excluded
        # before ranking or merely flagged.
        "prefilter_screen": prefilter is not None,
        "universe_id": resolved_universe_id,
        # FUND-UI-2: the EXACT membership this run graded, plus its order-insensitive
        # fingerprint. A saved list is an editable ticker list now, so an id alone dates
        # badly — `my_portfolio_v1` in June and in August are different cohorts, and a
        # rank verdict is universe-relative (a name's position is a statement about the
        # names it was ranked against). The members are what INPUT, not what survived:
        # excluded and unrateable names belong to the graded cohort too. Recorded with no
        # UI ceremony — it needs none to do its job.
        "universe_members": list(universe),
        "universe_member_hash": member_hash(list(universe)),
        "run_id": run_id,
        "council_mode": executed_mode,
        "council_runs_on": runs_on,
        "narrate_coverage": narrate_coverage,
        "ranker_only": ranker_only,
        # VALBAND-1: whether the opt-in absolute valuation band was computed this run
        # (a context column, never a verdict input). False -> no band section anywhere.
        "with_valuation_band": with_valuation_band,
        "universe_size": len(universe),
        "ranked_count": len(live),
        "shortlist": [r.ticker for r in shortlist],
        "est_cost": est,
        "fetch_error_count": len(fetch_errors),
    }
    result = RankPipelineResult(
        ranked=live, excluded=excluded, unrateable=unrateable, narratives=narratives,
        header=_pipeline_header(executed_mode), meta=meta, council_mode=executed_mode,
        council=council, shortlist=[r.ticker for r in shortlist],
        fetch_errors=fetch_errors, screen_bases=screen_bases, names=names,
        # REPORT-1: the per-rule record and the strategies that produced it, so the
        # "Rules applied" block reads from what ACTUALLY RAN (overrides included).
        screen_outcomes=screen_outcomes, rank_strategy=rank_strategy,
        screen_strategy=screen_strategy)

    if csv_path and not ranker_only and mode != "narrator":
        _append_agreement_csv(result, Path(csv_path))
    return result


def _resolve_universe(universe, universe_id, universes_dir: Path
                      ) -> tuple[list[str], str, str]:
    """Turn (universe list, universe_id) into (tickers, recorded_id, display_name). A
    ``universe_id`` with no explicit list loads the named manifest; an explicit list
    keeps its ``universe_id`` if given, else gets an ``adhoc:<hex8>`` fingerprint.

    The manifest's ``display_name`` rides along (REPORT-1) so a report can lead with
    "Defensive Income" and keep the id as the record key beside it. Empty for an ad-hoc
    list — a renderer then shows the id alone rather than inventing a name."""
    from .universe import adhoc_universe_id, load_universe_by_id
    if universe_id and not universe:
        u = load_universe_by_id(universe_id, universes_dir)
        return list(u.tickers), u.id, (getattr(u, "display_name", "") or "")
    if universe:
        name = ""
        if universe_id:
            try:
                name = getattr(load_universe_by_id(universe_id, universes_dir),
                               "display_name", "") or ""
            except Exception:
                name = ""            # an ad-hoc / unresolvable id keeps no display name
        return list(universe), (universe_id or adhoc_universe_id(list(universe))), name
    raise ValueError("run_rank_pipeline needs an explicit `universe` list or a "
                     "`universe_id` naming a manifest")


def load_rank_strategy_from_id(strategy_id: str, strategies_dir: Path):
    from .strategy.rank_loader import load_rank_strategy
    return load_rank_strategy(_resolve_strategy_path(strategy_id, strategies_dir))


def load_screen_from_id(screen_id: str, strategies_dir: Path):
    from .strategy.loader import load_strategy
    return load_strategy(_resolve_strategy_path(screen_id, strategies_dir))


def _build_adapter(*, today: date, use_cache: bool):
    from .data.provider import select_market_adapter
    from .data.retry import RetryAdapter
    # Armor the raw provider (retry+classify transient failures), THEN wrap in the cache
    # so a cache hit skips both the network and the retry (ITEM 5 — cache consulted first).
    adapter = RetryAdapter(select_market_adapter())
    if use_cache:
        from .data.cache import DEFAULT_CACHE_DIR, CachingAdapter
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)
    return adapter


def _append_agreement_csv(result: RankPipelineResult, path: Path) -> None:
    import csv
    new = not path.exists()
    rows = agreement_csv_rows(result)
    with path.open("a", newline="", encoding="utf-8") as fh:
        # QUOTE_MINIMAL (explicit) — a comma in dissent_notes quotes that FIELD only,
        # never the whole row (ITEM 5).
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                           ["ticker", "ranker_verdict", "council_verdict",
                            "agreement", "council_mode", "dissent_notes"],
                           quoting=csv.QUOTE_MINIMAL)
        if new:
            w.writeheader()
        for row in rows:
            w.writerow(row)


# Source-tag -> human label for the factor-integrity block (ITEM 1).
_SOURCE_LABELS = {
    "ev": "EV",
    "computed": "computed",
    "abstained": "abstained",
    "fallback:ebit_mcap": "EBIT/mcap proxy",
    "fallback:pe": "1/PE",
    "fallback:dividend_yield": "dividend-yield",
}


def _source_label(src: str) -> str:
    return _SOURCE_LABELS.get(src, src)


def factor_integrity(result: RankPipelineResult) -> list[dict]:
    """Per-factor source breakdown across the RANKED names (ITEM 1). For each factor:
    ``{factor, total, by_source: {source_tag: [tickers]}}`` — the data behind the
    'Factor integrity' disclosure block, so three runtime states (EV / EBIT-mcap proxy /
    abstained) that used to render identically now read in plain text."""
    ranked = result.ranked
    if not ranked:
        return []
    order = list(ranked[0].factor_sources) or list(ranked[0].factor_values)
    out: list[dict] = []
    for name in order:
        by_source: dict[str, list[str]] = {}
        for r in ranked:
            src = r.factor_sources.get(name)
            if src is None:                    # no tag recorded -> derive from presence
                src = "computed" if r.factor_values.get(name) is not None else "abstained"
            by_source.setdefault(src, []).append(r.ticker)
        out.append({"factor": name, "total": len(ranked), "by_source": by_source})
    return out


def format_integrity_entry(e: dict) -> str:
    """One factor's source breakdown as a string, e.g.
    'EV 21/23 · EBIT/mcap proxy 2/23 (HD, CAT) · abstained 0'. Fallback tickers are named
    when ≤5, counted otherwise. Shared by the CLI report and the Universe Run tab."""
    total, bs = e["total"], e["by_source"]
    primary = [s for s in bs if not s.startswith("fallback:") and s != "abstained"]
    fallbacks = [s for s in bs if s.startswith("fallback:")]
    parts = [f"{_source_label(s)} {len(bs[s])}/{total}" for s in primary]
    for s in fallbacks:
        tks = bs[s]
        named = f" ({', '.join(tks)})" if len(tks) <= 5 else ""
        parts.append(f"{_source_label(s)} {len(tks)}/{total}{named}")
    ab = bs.get("abstained", [])
    named = f" ({', '.join(ab)})" if 0 < len(ab) <= 5 else ""
    parts.append(f"abstained {len(ab)}{named}")
    return " · ".join(parts)


# REPORT-1: "Factor integrity" was internal jargon and told a reader nothing about what
# it was for. The section is now "Where the numbers came from", and each line is a
# sentence rather than a slash-separated tally. The COUNTS are exactly the same.
PROVENANCE_SECTION_TITLE = "Where the numbers came from"
PROVENANCE_SECTION_NOTE = (
    "How each ranked name's value for each factor was actually produced — a silent "
    "fallback (a proxy, a stale cache, a missing field) reads in plain text here "
    "instead of looking identical to a real measurement.")

# How a source tag reads INSIDE a sentence ("real data for all 10 names"). Same tags as
# _SOURCE_LABELS, phrased as prose rather than as a column heading.
_SOURCE_PHRASE = {
    "ev": "from enterprise value",
    "computed": "real data",
    "abstained": "no usable data",
    "fallback:ebit_mcap": "from the EBIT / market-cap proxy",
    "fallback:pe": "from 1 / price-earnings",
    "fallback:dividend_yield": "taken from dividend yield",
}


def _source_phrase(src: str) -> str:
    return _SOURCE_PHRASE.get(src, _source_label(src))


def _names_clause(tickers: list[str], total: int) -> str:
    """``"for all 10 names"`` / ``"for 2 names (HD, CAT)"`` — names them when few enough
    to be actionable, counts them otherwise."""
    n = len(tickers)
    if n == total:
        return f"for all {total} name{'s' if total != 1 else ''}"
    named = f" ({', '.join(tickers)})" if n <= 5 else ""
    return f"for {n} of {total} names{named}"


def provenance_sentences(result: RankPipelineResult) -> list[dict]:
    """``[{factor, sentence}]`` — one plain sentence per factor (REPORT-1), e.g.
    "Price volatility — real data for all 10 names." Reads the SAME
    ``factor_integrity`` counts; only the words change."""
    from .factors import FACTOR_REGISTRY
    out = []
    for e in factor_integrity(result):
        total, bs = e["total"], e["by_source"]
        label = getattr(FACTOR_REGISTRY.get(e["factor"]), "label", "") or e["factor"]
        parts = []
        for src in sorted(bs, key=lambda s: (s == "abstained", s)):
            parts.append(f"{_source_phrase(src)} {_names_clause(bs[src], total)}")
        out.append({"factor": e["factor"], "label": label,
                    "sentence": f"{label} — " + "; ".join(parts) + "."})
    return out


def format_factor_integrity(result: RankPipelineResult) -> list[str]:
    """The provenance block as CLI lines (see ``provenance_sentences``)."""
    entries = provenance_sentences(result)
    if not entries:
        return []
    lines = [f"  {PROVENANCE_SECTION_TITLE.upper()}:"]
    for e in entries:
        lines.append(f"      {e['sentence']}")
    return lines


# Screen-criterion measurement-basis disclosure (payout-on-FCF). Payout is a SCREEN
# criterion, not a rank factor, so its basis reads across ALL screened names (ranked or
# excluded), not just the ranked ones — its own line, same format as factor integrity.
_BASIS_DISPLAY = {"fcf": "FCF (4y mean)", "eps": "EPS fallback"}
# The same bases in prose, for a sentence rather than a column (REPORT-1).
_BASIS_PROSE = {"fcf": "4-year average free cash flow",
                "eps": "earnings per share (a marked fallback)",
                "abstained": "nothing measurable"}


def basis_phrase(basis: str) -> str:
    """A measurement basis as it reads inside a sentence, e.g. "4-year average free cash
    flow". Falls back to the column form for a basis with no prose entry."""
    return _BASIS_PROSE.get(basis, _BASIS_DISPLAY.get(basis, basis))


def screen_basis_integrity(result: RankPipelineResult) -> list[dict]:
    """Per screen-criterion, the count of names measured on each basis across the whole
    screened universe: ``[{criterion, total, by_basis: {basis: [tickers]}}]``."""
    by_criterion: dict[str, dict[str, list[str]]] = {}
    for ticker, crit_bases in result.screen_bases.items():
        for crit, basis in crit_bases.items():
            by_criterion.setdefault(crit, {}).setdefault(basis, []).append(ticker)
    out = []
    for crit in sorted(by_criterion):
        by_basis = by_criterion[crit]
        total = sum(len(v) for v in by_basis.values())
        out.append({"criterion": crit, "total": total, "by_basis": by_basis})
    return out


def format_screen_basis_entry(e: dict) -> str:
    """One criterion's basis breakdown, e.g.
    'FCF (4y mean) 11/13 · EPS fallback 1/13 (X) · abstained 1 (Y)' — primary basis first,
    marked fallbacks (with names when ≤5) next, abstentions last as a bare count."""
    total, bb = e["total"], e["by_basis"]
    parts = []
    if "fcf" in bb:
        parts.append(f"{_BASIS_DISPLAY['fcf']} {len(bb['fcf'])}/{total}")
    for b in sorted(x for x in bb if x not in ("fcf", "abstained")):
        tks = bb[b]
        named = f" ({', '.join(tks)})" if len(tks) <= 5 else ""
        parts.append(f"{_BASIS_DISPLAY.get(b, b)} {len(tks)}/{total}{named}")
    if "abstained" in bb:
        tks = bb["abstained"]
        named = f" ({', '.join(tks)})" if len(tks) <= 5 else ""
        parts.append(f"abstained {len(tks)}{named}")
    return " · ".join(parts)


def _abstention_reason(note: str) -> str:
    """Concise reason from a criterion's abstention note for the footnote."""
    r = note
    if r.startswith("not evaluated:"):
        r = r[len("not evaluated:"):].strip()
    return r.split(" — ")[0].strip()


def ranked_abstention_footnotes(result: RankPipelineResult) -> list[str]:
    """One footnote per ranked name whose screen criterion ABSTAINED (ITEM 3), e.g.
    '† PEP — screen criterion not evaluated: max_payout_ratio_fcf (mean FCF ≤ 0)'. A BUY
    whose dividend-safety check abstained is legitimate (abstention never excludes) but
    must be visible."""
    lines = []
    for r in result.ranked:
        for crit, note in sorted(r.screen_abstentions.items()):
            lines.append(f"† {r.ticker} — screen criterion not evaluated: "
                         f"{crit} ({_abstention_reason(note)})")
    return lines


def format_screen_basis(result: RankPipelineResult) -> list[str]:
    """The screen-criterion basis block as text lines, e.g.
    'payout (max_payout_ratio_fcf): FCF 14/16 · EPS fallback 2/16 (KMB, PEP)'."""
    entries = screen_basis_integrity(result)
    if not entries:
        return []
    lines = ["=== SCREEN BASIS (measurement basis across the screened names) ==="]
    for e in entries:
        lines.append(f"  {e['criterion']}: " + format_screen_basis_entry(e))
    return lines


# --------------------------------------------------------------------------- #
# REPORT-1 — the report's own header, summary and exclusion sentences
# --------------------------------------------------------------------------- #
def header_lines(result) -> list[str]:
    """The header block, human names first and ids second (REPORT-1).

    It used to be machine ids only — ``strategy conservative_plus_v1, universe
    defensive_income_16_v1`` — which told a reader nothing they could act on while
    hiding the names the UI already knew. The ids stay: they are the stable record
    keys a verdict log and a frozen run join on. They are just no longer the only
    thing shown. The run id sits LAST, muted, for the same reason."""
    m = result.meta or {}
    size = m.get("universe_size", len(getattr(result, "ranked", []) or []))
    universe = label_with_id(m.get("universe_name", ""), m.get("universe_id", "") or "")
    lines = [f"{universe} — {size} names" if universe else f"{size} names"]
    strategy = label_with_id(m.get("rank_strategy_name", ""),
                             m.get("rank_strategy_id", "") or "")
    if strategy:
        lines.append(f"Strategy: {strategy}")
    mode = m.get("council_mode", "")
    mode_phrase = ("ranker only, no AI commentary" if mode == "ranker-only"
                   else f"{mode} commentary" if mode else "")
    if mode_phrase:
        lines.append(f"Run: {mode_phrase}")
    return lines


def summary_line(result) -> str:
    """``"2 BUY · 6 HOLD · 2 SELL — 10 of 16 names ranked, 6 excluded by the screen"``.

    Derived from the result every time. There was no summary anywhere before this: a
    reader had to count the table by hand to learn what the run had concluded."""
    m = result.meta or {}
    return format_summary_line(
        result.ranked, universe_size=m.get("universe_size", len(result.ranked)),
        excluded=len(getattr(result, "excluded", []) or []),
        unrateable=len(getattr(result, "unrateable", []) or []),
        fetch_errors=len(getattr(result, "fetch_errors", []) or []))


def exclusion_sentence(result, ticker: str, reason: str) -> str:
    """One excluded name as a SENTENCE: the rule in words, the observed value and the
    limit, both in their proper units.

    The raw form was ``screen: min_dividend_yield (observed 0.009547 vs threshold
    0.015)`` — three machine identifiers and two raw decimals. This reads

        Walmart (WMT) — dividend yield 0.95%; the rule requires at least 1.5%.
                        [min_dividend_yield]

    The observed value, the threshold and the pass/fail decision are IDENTICAL — only
    the words change. Falls back to the raw reason for an exclusion that is not a screen
    rule (a market-cap floor, a sector or asset-kind gate, an unrateable name): those
    already read as prose and have no criterion to look up."""
    from .tools.criteria.registry import REGISTRY

    outcome = _failing_outcome(result, ticker, reason)
    if outcome is None:
        return reason
    name, o = outcome
    crit = REGISTRY.get(name)
    spec = crit.threshold_param if crit is not None else None
    unit = getattr(spec, "unit", "") or UNIT_RATIO
    currency = getattr(spec, "currency", None)
    comparison = getattr(crit, "comparison", COMPARISON_MIN)
    template = (getattr(crit, "observation", "")
                or (getattr(crit, "label", "") or name) + " {observed}")
    observed = template.format(
        observed=format_value(o["observed"], unit, currency=currency),
        signed=format_signed_change(o["observed"], unit))
    limit = format_limit_clause(comparison, o["threshold"], unit, currency=currency)
    tail = ""
    basis = o.get("basis") or ""
    if basis and basis != "abstained":
        tail += f" Measured on {basis_phrase(basis)}."
    if o.get("borderline"):
        tail += " This is a borderline miss — it is still a miss."
    return f"{observed}; {limit}.{tail}"


def _failing_outcome(result, ticker: str, reason: str):
    """``(criterion, outcome)`` for the rule that EXCLUDED this name, or None.

    Read from ``screen_outcomes`` — the per-criterion record of the same single screen
    evaluation the reason string came from — and matched by the criterion the reason
    NAMES, so the sentence can never describe a different rule from the one that fired."""
    if not reason.startswith(_SCREEN_REASON_PREFIX):
        return None
    named = reason[len(_SCREEN_REASON_PREFIX):].split(" (")[0].strip()
    per_name = (getattr(result, "screen_outcomes", None) or {}).get(ticker) or {}
    o = per_name.get(named)
    if o is None or o.get("passed") is not False:
        return None
    return named, o


_SCREEN_REASON_PREFIX = "screen: "


def exclusion_rows(result) -> list[dict]:
    """``[{ticker, name, sentence, criterion, flag}]`` per excluded name — the ONE
    source every surface renders, in the order the pipeline recorded them.

    ``flag`` is a standalone warning (e.g. the price-divergence disclosure), lifted OUT
    of the middle of the sentence into its own clearly-marked line: it is a separate
    statement about the name, not a clause of the rule it failed."""
    rows = []
    for ticker, reason in (getattr(result, "excluded", None) or []):
        body, flag = _split_flag(reason)
        outcome = _failing_outcome(result, ticker, body)
        rows.append({
            "ticker": ticker,
            "name": _disp(result, ticker),
            "sentence": exclusion_sentence(result, ticker, body),
            "criterion": outcome[0] if outcome else "",
            "flag": flag,
        })
    return rows


def _split_flag(reason: str) -> tuple[str, str]:
    """Split a trailing ``[⚠ …]`` disclosure off an exclusion reason. The reason string
    itself is NEVER rewritten — this only decides where each half renders."""
    i = reason.find("[⚠")
    if i == -1:
        return reason, ""
    return reason[:i].rstrip(), reason[i:].strip()


def format_exclusions(result) -> list[str]:
    """The excluded block as CLI lines (see ``exclusion_rows``)."""
    rows = exclusion_rows(result)
    if not rows:
        return []
    lines = ["  EXCLUDED — did not pass a rule, so was never ranked:"]
    for r in rows:
        muted = f"  [{r['criterion']}]" if r["criterion"] else ""
        lines.append(f"      {r['name']} — {r['sentence'].rstrip()}{muted}")
        if r["flag"]:
            lines.append(f"          {r['flag']}")
    return lines


def untested_rule_notes(result) -> list[dict]:
    """``[{ticker, name, verdict, rules: [sentence]}]`` for every RANKED name that
    passed the screen while a rule could not be tested at all.

    Duke Energy is the top-ranked BUY and Southern a HOLD, yet for both one rule
    (dividends vs free cash flow) was uncomputable — free cash flow was zero or
    negative. Passing four of five rules with the fifth untestable is materially
    different from passing all five, and it used to be a bare dagger. The reason text is
    the criterion's own, unchanged; it just stops hiding behind a symbol."""
    from .tools.criteria.registry import REGISTRY

    out = []
    for r in result.ranked:
        if r.excluded or not r.screen_abstentions:
            continue
        rules = []
        for crit, note in sorted(r.screen_abstentions.items()):
            label = getattr(REGISTRY.get(crit), "label", "") or crit
            rules.append(f"{label} ({_abstention_reason(note)}) [{crit}]")
        out.append({"ticker": r.ticker, "name": _disp(result, r.ticker),
                    "verdict": r.verdict.upper(), "rules": rules})
    return out


def format_untested_rules(result) -> list[str]:
    """The untested-rule disclosures as CLI lines (see ``untested_rule_notes``)."""
    notes = untested_rule_notes(result)
    if not notes:
        return []
    lines = ["  RULES THAT COULD NOT BE TESTED (the name still passed the screen):"]
    for n in notes:
        count = len(n["rules"])
        lines.append(f"      {n['name']} — {n['verdict']} — {count} rule"
                     f"{'s' if count != 1 else ''} could not be tested:")
        for rule in n["rules"]:
            lines.append(f"          {rule}")
    return lines


# --------------------------------------------------------------------------- #
# REPORT-1 — "Rules applied": what was filtered out, and on what basis
# --------------------------------------------------------------------------- #
RULES_SECTION_TITLE = "Rules applied"
PREFILTER_NOTE = ("Used as a prefilter — names failing any rule below were never "
                  "ranked.")
NON_PREFILTER_NOTE = ("Applied as a lens for commentary only — a name failing a rule "
                      "below was still ranked.")
NO_SCREEN_NOTE = ("This strategy screens nothing: no rule filtered the cohort, and "
                  "quality enters only through the ranking below.")


@dataclass(frozen=True)
class AppliedRule:
    """One screen rule as the report states it: the human name, the threshold in words,
    and what it actually did across the cohort."""

    criterion: str                 # the registry id — the record key, kept muted
    label: str                     # Criterion.label, e.g. "Dividend yield"
    threshold_phrase: str          # "at least 1.5%" / "at most 80%" / "no worse than -10%"
    passed: int = 0
    failed: int = 0
    not_tested: int = 0
    # How this rule was actually MEASURED across the cohort, when it reports a basis
    # (e.g. cash-flow vs the marked EPS fallback). This is the disclosure the separate
    # "Screen basis" section used to carry; the block absorbs it rather than dropping it.
    measured: str = ""

    @property
    def tally(self) -> str:
        """``"passed 11 · failed 3 · not tested 2"`` — zero categories omitted, but a
        rule that nothing failed still renders its "passed N". Silence about a rule is
        the thing this block exists to end."""
        parts = [f"passed {self.passed}"] if self.passed else []
        if self.failed:
            parts.append(f"failed {self.failed}")
        if self.not_tested:
            parts.append(f"not tested {self.not_tested}")
        return " · ".join(parts) or "not applied to any name"


@dataclass(frozen=True)
class RulesApplied:
    """The whole block: the screen and its rules, then the ranker's own filters."""

    screen_label: str = ""         # "Conservative (defensive) screen"
    screen_id: str = ""            # "conservative_screen_v1"
    prefilter: bool = False
    rules: list[AppliedRule] = field(default_factory=list)
    ranker_lines: list[str] = field(default_factory=list)

    @property
    def screen_heading(self) -> str:
        if not self.screen_id:
            return "Screen: none"
        return f"Screen: {label_with_id(self.screen_label, self.screen_id)}"

    @property
    def screen_note(self) -> str:
        if not self.screen_id:
            return NO_SCREEN_NOTE
        return PREFILTER_NOTE if self.prefilter else NON_PREFILTER_NOTE


def rules_applied(result) -> Optional[RulesApplied]:
    """Every rule this run applied, read from the strategy objects that ACTUALLY RAN.

    The header used to say only ``screen conservative_screen_v1``. That screen holds SIX
    rules; the report only ever named the three that something failed, so a reader could
    not tell what had been applied, what a rule's limit was, or that three further rules
    had been checked and passed by everything. This block states all of it BEFORE any
    result.

    Nothing here is hardcoded: the rules come from ``result.screen_strategy.criteria``,
    their names and units from the registry, and their tallies from
    ``result.screen_outcomes`` — the per-criterion record the single screen evaluation
    already produced. A strategy edited tomorrow (a rule added, a threshold moved, a
    per-run override applied) changes this block with no code change.

    Returns None only when the run carries neither a screen nor a rank strategy to
    describe (a hand-built result), so such a report renders exactly as before."""
    from .tools.criteria.registry import REGISTRY

    screen = getattr(result, "screen_strategy", None)
    rank = getattr(result, "rank_strategy", None)
    outcomes = getattr(result, "screen_outcomes", None) or {}
    bases = getattr(result, "screen_bases", None) or {}
    selections = list(getattr(screen, "criteria", None) or [])
    if not selections and (outcomes or bases):
        # A result that carries per-rule EVIDENCE but no strategy object (a hand-built
        # or replayed result). The rules are still named and their measurement basis
        # still disclosed — losing a disclosure because an object is absent would be
        # exactly the silent-failure hole this block exists to close. Thresholds come
        # from the evidence when it has them, and are omitted when it does not.
        selections = [_SelectionFromEvidence(n, _recorded_threshold(outcomes, n))
                      for n in _criteria_seen(outcomes, bases)]
    if screen is None and rank is None and not selections:
        return None

    rules: list[AppliedRule] = []
    for sel in selections:
        name = getattr(sel, "name", "")
        threshold = getattr(sel, "threshold", None)
        crit = REGISTRY.get(name)
        label = getattr(crit, "label", "") or name
        comparison = getattr(crit, "comparison", COMPARISON_MIN)
        spec = crit.threshold_param if crit is not None else None
        phrase = ("limit not recorded" if threshold is None else format_threshold(
            comparison, threshold, getattr(spec, "unit", "") or UNIT_RATIO,
            currency=getattr(spec, "currency", None)))
        passed = failed = not_tested = 0
        for per_name in outcomes.values():
            o = per_name.get(name)
            if o is None:
                continue
            if o["passed"] is True:
                passed += 1
            elif o["passed"] is False:
                failed += 1
            else:
                not_tested += 1          # NOT-EVAL is not a fail (house rule 3)
        rules.append(AppliedRule(
            criterion=name, label=label, threshold_phrase=phrase, passed=passed,
            failed=failed, not_tested=not_tested,
            measured=_measured_phrase(result, name)))

    return RulesApplied(
        screen_label=(result.meta.get("screen_strategy_name", "") if result.meta else ""),
        screen_id=(getattr(screen, "id", "") if screen is not None else ""),
        prefilter=bool((result.meta or {}).get("prefilter_screen")),
        rules=rules, ranker_lines=_ranker_filter_lines(rank))


@dataclass(frozen=True)
class _SelectionFromEvidence:
    """A criterion selection reconstructed from a result's own per-rule evidence, for a
    result that carries no strategy object."""

    name: str
    threshold: object = None


def _criteria_seen(outcomes: dict, bases: dict) -> list[str]:
    """Every criterion this result has evidence for, first-seen order preserved so the
    block's row order is deterministic."""
    seen: list[str] = []
    for per_name in list(outcomes.values()) + list(bases.values()):
        for crit in per_name:
            if crit not in seen:
                seen.append(crit)
    return seen


def _recorded_threshold(outcomes: dict, criterion: str):
    """The threshold this run actually applied for a criterion, taken from the evidence.
    None when nothing recorded one — then the block says so rather than inventing it."""
    for per_name in outcomes.values():
        o = per_name.get(criterion)
        if o is not None and o.get("threshold") is not None:
            return o["threshold"]
    return None


def _measured_phrase(result, criterion: str) -> str:
    """How one rule was measured across the screened names, in words — e.g. "4-year
    average free cash flow for 14 names; earnings per share (a marked fallback) for 2
    (KMB, PEP)". Empty when the rule reports no basis (most do not)."""
    by_basis: dict[str, list[str]] = {}
    for ticker, crit_bases in (getattr(result, "screen_bases", None) or {}).items():
        basis = crit_bases.get(criterion)
        if basis:
            by_basis.setdefault(basis, []).append(ticker)
    if not by_basis or set(by_basis) <= {"abstained"}:
        return ""
    parts = []
    for basis in sorted(by_basis, key=lambda b: (b == "abstained", b != "fcf", b)):
        tks = sorted(by_basis[basis])
        named = f" ({', '.join(tks)})" if len(tks) <= 5 else ""
        parts.append(f"{basis_phrase(basis)} for {len(tks)} name"
                     f"{'s' if len(tks) != 1 else ''}{named}")
    return "; ".join(parts)


_CUT_PHRASE = {
    "quintile": "top 20% BUY, bottom 20% SELL, middle HOLD (quintile cut)",
    "top_k": "the best {k} names BUY, the rest HOLD (top-k cut)",
    "top_percentile": "the best {p} BUY, the rest HOLD (top-percentile cut)",
}
_MISSING_PHRASE = {
    "worst": "Missing factor values are ranked worst.",
    "neutral": ("A name missing a factor value is judged on the factors it does have "
                "(its rank for the missing one is imputed from its others), never "
                "dumped to the bottom for the gap."),
    "exclude": "A name missing any factor value is dropped before ranking.",
}


def _ranker_filter_lines(rank) -> list[str]:
    """The RANKER's own filters, in the same plain register as the screen's rules — the
    cut, the factors it ranks on, the market-cap floor, any sector scope, and how a
    missing value is treated. These decide outcomes exactly as the screen's rules do, so
    leaving them unstated would reproduce the problem one level down."""
    from .factors import FACTOR_REGISTRY
    if rank is None:
        return []
    lines: list[str] = []
    cut = getattr(rank, "cut", "quintile") or "quintile"
    phrase = _CUT_PHRASE.get(cut, cut).format(
        k=getattr(rank, "k", ""),
        p=f"{(getattr(rank, 'percentile', 0.0) or 0.0):.0%}")
    lines.append(f"Ranking: {phrase}.")
    labels = [(FACTOR_REGISTRY[f.name].label if f.name in FACTOR_REGISTRY else f.name)
              for f in (getattr(rank, "factors", None) or [])]
    if labels:
        lines.append("Names ranked on: " + ", ".join(labels) + ".")
    floor = getattr(rank, "min_market_cap", None)
    if floor:
        lines.append("Company size: at least "
                     + format_value(floor, UNIT_CURRENCY, currency="USD")
                     + " (applied by the ranker, before the screen).")
    excluded_sectors = list(getattr(rank, "exclude_sectors", None) or [])
    if excluded_sectors:
        lines.append("Sectors excluded: " + ", ".join(excluded_sectors) + ".")
    included_sectors = list(getattr(rank, "include_sectors", None) or [])
    if included_sectors:
        lines.append("Sectors admitted: " + ", ".join(included_sectors)
                     + " (a name outside them is not ranked).")
    kinds = list(getattr(rank, "asset_kinds", None) or [])
    if kinds:
        lines.append("Asset kinds admitted: " + ", ".join(kinds) + ".")
    payout = getattr(rank, "max_payout_ratio", None)
    if payout:
        lines.append("Dividends vs earnings: at most "
                     + format_value(payout, UNIT_PERCENT)
                     + " (applied by the ranker).")
    missing = getattr(rank, "missing", "worst") or "worst"
    lines.append(_MISSING_PHRASE.get(missing,
                                     f"Missing factor values are handled: {missing}."))
    return lines


def format_rules_applied(result) -> list[str]:
    """The rules block as CLI lines — the same content every other surface renders."""
    block = rules_applied(result)
    if block is None:
        return []
    lines = [f"  {RULES_SECTION_TITLE.upper()}", f"      {block.screen_heading}",
             f"      {block.screen_note}"]
    if block.rules:
        lines.append("")
        width = max(len(r.label) for r in block.rules)
        phrase_width = max(len(r.threshold_phrase) for r in block.rules)
        for r in block.rules:
            lines.append(f"      {r.label:<{width}}  "
                         f"{r.threshold_phrase:<{phrase_width}}  {r.tally}"
                         f"  [{r.criterion}]")
            if r.measured:
                lines.append(f"      {'':<{width}}  {'':<{phrase_width}}  "
                             f"measured on {r.measured}")
    if block.ranker_lines:
        lines.append("")
        lines += [f"      {line}" for line in block.ranker_lines]
    return lines


# --------------------------------------------------------------------------- #
# PRICE-1 — the share price + 52-week position, ALWAYS ON
# --------------------------------------------------------------------------- #
PRICE_SECTION_TITLE = "Share price & 52-week position"
PRICE_SECTION_NOTE = (
    "The last close in the name's OWN quoted currency (never converted) with the date "
    "of that close, and where it sits between the trailing 52-week low and high. "
    "Display only — not ranked, not screened.")


def price_rows(result) -> list[tuple[str, str]]:
    """``(display name, price line)`` per RATEABLE name (PRICE-1) — the ONE source the
    CLI block, the Run tab table, the canonical markdown and the HTML export all render,
    so the four cannot drift.

    NOT gated by the valuation-band flag: the price comes off the 400-day bars every run
    already fetches, so it costs nothing and is always shown. Abstentions are INCLUDED
    ("price not available — …", "52-week range not evaluated — only 31 weeks of closes"):
    an honest reason is the point, and silence is indistinguishable from a feature that
    was never switched on. Empty only when NO name carries a price at all (a hand-built
    or pre-PRICE-1 result), so such a run renders exactly what it rendered before."""
    return [(_disp(result, r.ticker), r.price.display) for r in result.ranked
            if not r.excluded and getattr(r, "price", None) is not None]


def format_price_lines(result) -> list[str]:
    """The share-price block as CLI lines (see ``price_rows``)."""
    rows = price_rows(result)
    if not rows:
        return []
    lines = [f"  {PRICE_SECTION_TITLE.upper()} (quoted currency, never converted — "
             "not ranked, not screened):"]
    for name, line in rows:
        lines.append(f"      {_name_col(name)} {line}")
    return lines


# --------------------------------------------------------------------------- #
# VALBAND-1 / PRICE-1 / PRICE-2 — the valuation band as a TABLE
# --------------------------------------------------------------------------- #
VALUATION_BAND_SECTION_TITLE = "Valuation band (absolute — vs each name's own history)"
# At most two sentences, ahead of the data. Everything else — the doctrine, the caveats —
# lives in the footnotes BELOW the table (PRICE-2): five lines of caveat before the first
# number is how a section stops being read.
VALUATION_BAND_INTRO = (
    "What each name costs today, where that sits in its own 12-month range, where "
    "today's multiple sits in its OWN multi-year range, and what the price would be at "
    "that name's own MEDIAN past multiple. Rows follow the ranked table's order — they "
    "are deliberately not re-sorted by gap.")
VALUATION_BAND_DOCTRINE = (
    "Arithmetic on each company's own history — not a forecast, not a target price and "
    "not a recommendation. A business that has permanently derated should trade below "
    "its own past median, and arithmetic cannot tell decline from mispricing. Not "
    "ranked, not screened, and never shown to any model.")
# Retained under its VALBAND-1 name for single-line surfaces and older callers.
VALUATION_BAND_SECTION_NOTE = VALUATION_BAND_DOCTRINE

_COL_NAME = "Name"
_COL_PRICE = "Price"
# REPORT-1: the separate "Share price & 52-week position" section is FOLDED IN here as
# two columns, so the same ten names are not listed twice in two sections — and the
# percentage-of-range is replaced by the two PRICES it was derived from, which is what a
# reader can actually act on ("$114.00 / $133.46" beats "34% of its range").
_COL_LOW_52W = "12-month low"
_COL_HIGH_52W = "12-month high"
_COL_MULTIPLE = "EV/EBIT"
_COL_PERCENTILE = "Percentile"
_COL_MONTHS = "Months"
_COL_REVERSION = "Reversion value"
_COL_GAP = "Gap"
# Columns whose content is a NUMBER and reads better flush right in fixed-width text.
_BAND_RIGHT_ALIGNED = frozenset({_COL_PRICE, _COL_LOW_52W, _COL_HIGH_52W,
                                 _COL_MULTIPLE, _COL_REVERSION, _COL_GAP})
_NOT_EVALUATED = "not evaluated"
_EMPTY = "—"


@dataclass(frozen=True)
class ValuationBandTable:
    """The price-and-valuation section as a TABLE — the ONE source the CLI, the Run tab,
    the canonical markdown and the HTML export all render (PRICE-2, extended by
    REPORT-1).

    ``rows`` are dicts keyed by ``columns``, in the RANKED TABLE'S ORDER so a reader
    comparing sections never has to re-find a name. Every cell is already a display
    string built from the SAME ``ValuationBand`` / ``ReversionValue`` / ``PriceContext``
    objects the single-line renderings use, so a reformat can never become a different
    value.

    ``has_band`` is False on a band-OFF run: the price and 12-month range columns are
    ALWAYS present (they cost nothing and are never gated), and the band/reversion
    columns simply do not exist. That is why the section carries its own ``title`` —
    the same table honestly describes itself as either."""

    columns: list[str]
    rows: list[dict[str, str]]
    intro: str = VALUATION_BAND_INTRO
    footnotes: list[str] = field(default_factory=list)
    has_band: bool = True

    @property
    def title(self) -> str:
        return (VALUATION_BAND_SECTION_TITLE if self.has_band
                else PRICE_SECTION_TITLE)

    @property
    def median_column(self) -> str:
        """The median column's header, e.g. ``"Own 5y median"``."""
        for c in self.columns:
            if c.startswith("Own ") and c.endswith(" median"):
                return c
        return "Own median"


def _multiple_cell(band) -> str:
    """``19.7x`` — or ``18.2x (P/E)`` on the band's labelled fallback basis, so the
    fallback stays disclosed even under an EV/EBIT column header."""
    if band.current is None:
        return _EMPTY
    tag = " (P/E)" if band.basis == "pe" else ""
    return f"{band.current:.1f}x{tag}"


def valuation_band_table(result) -> Optional[ValuationBandTable]:
    """The valuation-band table for a run, or None when no name carries a band.

    Every other block in a run report is a cohort statement: the ranked table says which
    of these names is least expensive, and in a uniformly hot cohort that is still a #1.
    This one says where each price sits against its OWN history — and, beside it, what
    that name would cost at its own median multiple, with today's price in the row so the
    gap has a visible base.

    ABSTENTIONS KEEP THEIR ROW: a name is never dropped, its cells read "not evaluated"
    and the row carries the REAL reason. Silence is indistinguishable from a feature that
    was never switched on, which is exactly the ambiguity VALBAND-1 was built to end.

    COVERAGE ("42 of 61 months") is stated ONCE as a footnote when it is identical for
    every rated name (it usually is — the months drop out on statement availability, which
    is a cohort-wide property of the window), and gets its own compact column only when it
    actually varies. Repeating it in ten rows says nothing ten times.

    Display only — nothing here ranks, screens, gates or votes."""
    from .tools.valuation_band import BAND_YEARS, ordinal, percentile_gloss
    from .tools.price_context import format_money

    # REPORT-1: a row for every rateable name that has EITHER a price or a band. The
    # price is never gated by the valuation-band toggle, so a band-off run still renders
    # this table — just without the band's own columns.
    live = [r for r in result.ranked
            if not r.excluded and (getattr(r, "valuation_band", None) is not None
                                   or getattr(r, "price", None) is not None)]
    if not live:
        return None
    has_band = any(getattr(r, "valuation_band", None) is not None for r in live)

    bands = [r.valuation_band for r in live if getattr(r, "valuation_band", None)]
    rated = [b for b in bands if b.available]
    window = rated[0].window_years if rated else BAND_YEARS
    median_col = f"Own {window}y median"
    coverage = {(b.months_covered, b.months_total) for b in rated}
    uniform = len(coverage) <= 1                      # one shared count -> a footnote

    columns = [_COL_NAME, _COL_PRICE, _COL_LOW_52W, _COL_HIGH_52W]
    if has_band:
        columns += [_COL_MULTIPLE, median_col, _COL_PERCENTILE]
        if not uniform:
            columns.append(_COL_MONTHS)
        columns += [_COL_REVERSION, _COL_GAP]

    rows: list[dict[str, str]] = []
    for r in live:
        band, rev, price = r.valuation_band, getattr(r, "reversion", None), \
            getattr(r, "price", None)
        cells = dict.fromkeys(columns, _EMPTY)
        cells[_COL_NAME] = _disp(result, r.ticker)
        cells[_COL_PRICE] = (format_money(price.last_close, price.currency)
                             if price is not None and price.available else _EMPTY)
        if price is not None and price.range_available:
            cells[_COL_LOW_52W] = format_money(price.low_52w, price.currency)
            cells[_COL_HIGH_52W] = format_money(price.high_52w, price.currency)
        elif price is not None and price.range_note:
            # The 52-week range abstained — say so, with its real span, rather than
            # leaving two blanks a reader would read as "no movement".
            cells[_COL_LOW_52W] = f"{_NOT_EVALUATED} — {price.range_note}"
        if band is None:
            pass                                  # band off: those columns do not exist
        elif band.available:
            cells[_COL_MULTIPLE] = _multiple_cell(band)
            cells[median_col] = (f"{band.median_multiple:.1f}x"
                                 if band.median_multiple is not None else _EMPTY)
            cells[_COL_PERCENTILE] = (f"{ordinal(round(band.percentile))} "
                                      f"({percentile_gloss(band.percentile)})")
            if not uniform:
                cells[_COL_MONTHS] = f"{band.months_covered} of {band.months_total}"
            if rev is not None and rev.available:
                cells[_COL_REVERSION] = format_money(rev.price, rev.currency)
                cells[_COL_GAP] = f"{rev.gap:+.0%}"
            elif rev is not None:
                # The band computed but the arithmetic could not — its OWN reason, in the
                # row, exactly as an abstaining band states its own.
                cells[_COL_REVERSION] = f"{_NOT_EVALUATED} — {rev.note}" if rev.note \
                    else _NOT_EVALUATED
        else:
            cells[_COL_PERCENTILE] = f"{_NOT_EVALUATED} — {band.note}" if band.note \
                else _NOT_EVALUATED
        rows.append(cells)

    return ValuationBandTable(
        columns=columns, rows=rows, has_band=has_band,
        intro=VALUATION_BAND_INTRO if has_band else PRICE_SECTION_NOTE,
        footnotes=_band_footnotes(live, uniform, coverage, has_band=has_band))


def _band_footnotes(live, uniform: bool, coverage: set, *,
                    has_band: bool = True) -> list[str]:
    """The notes that belong BELOW the table: the shared month coverage, the net-debt
    disclosure, then the doctrine in full. Nothing here is a caveat the reader has to get
    past to reach a number."""
    notes: list[str] = []
    stamps = sorted({r.price.as_of.isoformat() for r in live
                     if getattr(r, "price", None) is not None
                     and r.price.as_of is not None})
    if stamps:
        when = stamps[0] if len(stamps) == 1 else f"{stamps[0]} to {stamps[-1]}"
        notes.append(f"Prices are the last close on {when}, each in the name's own "
                     "quoted currency, never converted. A stale cache shows up here.")
    if not has_band:
        return notes                    # band off: no coverage, no doctrine to state
    if uniform and coverage:
        covered, total = next(iter(coverage))
        if covered < total:
            notes.append(
                f"Every rated name here is placed on the same {covered} of {total} "
                f"monthly observations in the window; the other {total - covered} months "
                "lack usable statements, so they drop out rather than being filled in.")
        else:
            notes.append(f"Every rated name here is placed on all {total} monthly "
                         "observations in the window.")
    held = sorted({r.ticker for r in live
                   if getattr(r, "valuation_band", None) is not None
                   and r.valuation_band.available
                   and r.valuation_band.net_debt_basis == "latest"})
    if held:
        notes.append("Net debt held at its latest reported value across the window "
                     f"(the provider gave no dated debt/cash) for: {', '.join(held)}.")
    notes.append(VALUATION_BAND_DOCTRINE)
    return notes


def format_valuation_bands(result) -> list[str]:
    """The valuation-band table as CLI lines — the SAME columns as every other surface,
    laid out as aligned fixed-width text (see ``valuation_band_table``)."""
    table = valuation_band_table(result)
    if table is None:
        return []
    lines = [f"  {VALUATION_BAND_SECTION_TITLE.upper()}:", f"      {table.intro}", ""]
    lines += [f"      {row}" for row in _fixed_width_table(table)]
    lines.append("")
    lines += [f"      - {note}" for note in table.footnotes]
    return lines


def _fixed_width_table(table: ValuationBandTable) -> list[str]:
    """A column-aligned rendering of ``table`` for the console: numeric columns flush
    right, text columns flush left, every column as wide as its widest cell. Nothing is
    truncated — an abstention reason widens its column rather than losing its tail."""
    widths = {c: max(len(c), *(len(r[c]) for r in table.rows)) if table.rows else len(c)
              for c in table.columns}

    def _right(col: str) -> bool:
        # the median column's header carries the window ("Own 5y median"), so it is
        # matched by shape rather than listed as a literal.
        return col in _BAND_RIGHT_ALIGNED or (col.startswith("Own ")
                                              and col.endswith(" median"))

    def _cell(col: str, text: str) -> str:
        return text.rjust(widths[col]) if _right(col) else text.ljust(widths[col])

    out = ["  ".join(_cell(c, c) for c in table.columns).rstrip()]
    out.append("  ".join("-" * widths[c] for c in table.columns))
    for row in table.rows:
        out.append("  ".join(_cell(c, row[c]) for c in table.columns).rstrip())
    return out


def _n_factors(result) -> int:
    """How many factors this run ranked on (for the score gloss)."""
    for r in result.ranked:
        if r.factor_ranks:
            return len(r.factor_ranks)
    return 0


def used_symbol_notes(result) -> list[tuple[str, str]]:
    """The table's symbol legend — one FULL SENTENCE per symbol, and ONLY for symbols
    this run actually used (REPORT-1).

    All three used to share a single dense run-on line, which is why none of them was
    read. Explaining a symbol that never appears is noise of a different kind, so the
    legend is filtered to what is on the page."""
    from .report_language import SYMBOL_NOTES
    used = set()
    if any(r.imputed_factors for r in result.ranked):
        used.add("*")
    if any(r.screen_abstentions for r in result.ranked):
        used.add("†")
    if boundary_tie_notes(result.ranked):
        used.add(BOUNDARY_FLAG)
    return [(sym, note) for sym, note in SYMBOL_NOTES if sym in used]


def format_ranked_factor_lines(result) -> list[str]:
    """The per-factor rank AND value for each ranked name, as CLI lines.

    The console table is a fixed-width three-column layout that predates the factor
    columns; rather than widen it past readability, the same cells
    ``ranked_table_rows`` builds are printed underneath it — the identical strings the
    Run tab, the markdown and the HTML render in their factor columns."""
    rows, factor_ids = ranked_table_rows(result.ranked, result.names)
    if not factor_ids or not rows:
        return []
    from .rank_engine import factor_column_label
    labels = [factor_column_label(f) for f in factor_ids]
    lines = ["  HOW EACH NAME RANKED ON EACH FACTOR (rank · value; 1 = best):"]
    for row in rows:
        cells = " · ".join(f"{lab.split(' (')[0]} {row[lab]}"
                           for lab in labels if lab in row)
        lines.append(f"      {row['Name']} — {cells}")
    lines.append("      Factor ids, in order: " + ", ".join(factor_ids) + ".")
    return lines


def format_cli_report(result: RankPipelineResult) -> str:
    """The console report the CLI prints — built from the structured result so the UI
    and CLI show the SAME thing. Mirrors the legacy run_pipeline.py layout."""
    m = result.meta
    # REPORT-1: human names first, ids second and muted; the run id last, as the record
    # key. The old first line was five machine ids and nothing a reader could act on.
    lines = list(header_lines(result))
    lines.append(result.header)
    if not m["ranker_only"]:
        lines.append(f"Shortlist: {len(m['shortlist'])} of {m['universe_size']} names · "
                     f"estimated cost ${m['est_cost']:.2f}")
    if m.get("run_id"):
        lines.append(f"Run id: {m['run_id']}")
    lines += ["", f"  {summary_line(result)}"]

    rules_block = format_rules_applied(result)        # REPORT-1 Part 1
    if rules_block:
        lines.append("")
        lines.extend(rules_block)

    lines += ["", "  RANKED — the verdict of record · "
                  + label_with_id(m.get("rank_strategy_name", ""),
                                  m["rank_strategy_id"])]
    lines.append(f"      {format_score_gloss(_n_factors(result), len(result.ranked))}")
    lines.append("")
    tie_notes = boundary_tie_notes(result.ranked)     # VERDICT-TIE-1 boundary marks
    positions = cohort_positions(result.ranked)      # tie-shared #N of M (RANK-DISPLAY-1)
    cohort_m = len(result.ranked)                     # rateable cohort size (M); NOT `m`
                                                      # (that is result.meta, used below)
    for r in result.ranked:
        disp = _disp(result, r.ticker) + ("†" if r.screen_abstentions else "")
        pos, tied = positions.get(r.ticker, (None, False))
        cell = format_position_cell(pos, cohort_m, tied, r.combined_rank,
                                    len(r.factor_ranks))
        # The boundary mark rides in the VERDICT cell (it qualifies the verdict, not the
        # score). A marked row is intentionally wider than the 5-char verdict column — a
        # tie that decided a verdict should break the eye's scan.
        verdict = format_verdict_cell(r.verdict, tie_notes.get(r.ticker, ""))
        lines.append(f"  {_name_col(disp):<34} {verdict:<5} {cell}")
    factor_lines = format_ranked_factor_lines(result)
    if factor_lines:
        lines.append("")
        lines.extend(factor_lines)
    symbols = [f"      {sym}  {note}" for sym, note in used_symbol_notes(result)]
    if symbols:
        lines.append("")
        lines.extend(symbols)
    untested = format_untested_rules(result)          # REPORT-1: the dagger, in words
    if untested:
        lines.append("")
        lines.extend(untested)
    integrity = format_factor_integrity(result)
    if integrity:
        lines.append("")
        lines.extend(integrity)
    band_block = format_valuation_bands(result)
    if band_block:
        lines.append("")
        lines.extend(band_block)
    exclusion_block = format_exclusions(result)       # REPORT-1: sentences, not fragments
    if exclusion_block:
        lines.append("")
        lines.extend(exclusion_block)
    if result.unrateable:
        lines.append("")
        lines.append("  NO USABLE DATA — no verdict was formed for these names:")
        for t, reason in result.unrateable:
            lines.append(f"      {_disp(result, t)} — {reason}")
    if result.fetch_errors:
        lines.append("")
        lines.append("  DATA FETCH FAILED — re-run to recover (a temporary "
                     "provider failure, not missing data):")
        for t, reason in result.fetch_errors:
            lines.append(f"      {_disp(result, t)} — {reason}")
    if not m["ranker_only"]:
        lines.append("")
        lines.append(format_narratives(result) if m["council_mode"] == "narrator"
                     else agreement_table(result))
    return "\n".join(lines)


def agreement_table(result: PipelineResult) -> str:
    """The per-name AGREEMENT TABLE — the evidence for the standing B->A review:
    how often the council dissents from the ranker and whether the dissents are
    useful."""
    lines = [f"AGREEMENT ({result.council_mode}) — ranker = verdict-of-record",
             f"  {'ticker':<10} {'ranker':<6} {'council':<8} {'agree?':<9} dissent"]
    for o in result.council:
        council = (o.council_verdict or "—").upper()
        agree = o.agreement or "(narrator)"
        d = "; ".join(o.dissent_notes) if o.dissent_notes else ""
        lines.append(f"  {o.ticker:<10} {o.ranker_verdict.upper():<6} {council:<8} "
                     f"{agree:<9} {d}")
    n = len(result.council)
    dis = sum(1 for o in result.council if o.agreement == "DISAGREE")
    lines.append(f"  -> {n} councils, {dis} DISAGREE "
                 f"({'second-opinion check active' if result.council_mode == 'second_opinion' else 'narrator: no independent verdict'})")
    return "\n".join(lines)


def format_narratives(result: PipelineResult) -> str:
    """The NARRATIVE section — in narrator mode this is the LLM's ENTIRE job, so it
    must be visible. One block per shortlisted name: the ranker verdict-of-record and
    the Decision agent's synthesis (factor ranks / strategy fit; anything beyond the
    snapshot phrased as an open question — no accounting reinterpretation)."""
    names = getattr(result, "names", {})
    lines = ["=== NARRATIVE (non-judging) ==="]
    if not result.council:
        lines.append("  (no names reached the council)")
    for o in result.council:
        d = o.report.decision
        narrative = (d.rationale.strip() if d and d.rationale else "") \
            or "(no narrative produced)"
        lines.append(f"\n{display_name(o.ticker, names.get(o.ticker))} — "
                     f"ranker verdict {o.ranker_verdict.upper()}")
        lines.append(narrative)
    return "\n".join(lines)


def agreement_csv_rows(result: PipelineResult) -> list[dict]:
    return [{
        "ticker": o.ticker, "ranker_verdict": o.ranker_verdict,
        "council_verdict": o.council_verdict or "",
        "agreement": o.agreement or "", "council_mode": result.council_mode,
        "dissent_notes": " | ".join(o.dissent_notes),
    } for o in result.council]


# --------------------------------------------------------------------------- #
# FUND-RUN-1 — ONE cohort, N strategies, ONE combined grid
# --------------------------------------------------------------------------- #
# Re-grading a cohort through five lenses meant five manual runs and five separate
# reports to eyeball side by side (hit 2026-08-04 and 2026-08-10). This runs the SAME
# deterministic stage once per strategy over the SAME cohort and combines the results
# into one grid. Strictly DETERMINISTIC: every per-strategy run is ranker-only, so a
# multi-lens re-grade costs nothing and narration stays where it was — a per-strategy,
# optional single-strategy run. No new decision logic: each column is exactly the
# verdict-of-record that a single run of that strategy would produce, and the grid only
# arranges them.
_RANKED, _EXCLUDED, _UNRATEABLE, _FETCH_ERROR, _ABSENT = (
    "ranked", "excluded", "unrateable", "fetch_error", "absent")


@dataclass
class MultiStrategyCell:
    """One (ticker, strategy) outcome — the cell of the combined grid.

    ``status`` is the axis the name landed on under THIS strategy: ranked (with its
    cohort position, verdict and rank-sum score), excluded (the failed rule + observed
    value, verbatim from the run), unrateable (no data — no verdict), fetch_error
    (transient; rerun), or absent (the strategy never reported the name)."""

    strategy_id: str
    status: str = _ABSENT
    position: Optional[int] = None
    cohort_size: int = 0
    verdict: str = ""
    score: Optional[float] = None
    # The RAW reason string, exactly as the run recorded it — the scoreboard and the
    # verdict publisher parse this shape, so it is never rewritten.
    reason: str = ""
    # The same exclusion in REPORT-1 plain English ("dividends took 120% of free cash
    # flow; the rule allows at most 80%"). Computed by ``combine_rank_results``, which
    # has the result the sentence needs; empty when there is no rule to name.
    reason_plain: str = ""

    def render(self) -> str:
        """The cell as one honest line — each axis reads distinctly (an exclusion is not
        a bad rank, and no-data is not an exclusion)."""
        if self.status == _RANKED:
            pos = f"#{self.position} of {self.cohort_size}" if self.position else "ranked"
            return f"{pos} · {self.verdict.upper()}"
        if self.status == _EXCLUDED:
            return f"excluded — {self.reason_plain or self.reason}"
        if self.status == _UNRATEABLE:
            # REPORT-2: "no data" is the cell's own axis — short enough to scan in a
            # grid, with the full reason kept in that lens's detail section below.
            return "no data"
        if self.status == _FETCH_ERROR:
            return "fetch failed (rerun)"
        return "—"


@dataclass
class MultiStrategyRow:
    """One name across every strategy in the run.

    ``rank_sum`` adds the per-strategy cohort POSITIONS over the strategies that actually
    RANKED the name — nothing is imputed for a strategy that excluded it (the null≠false
    discipline: an exclusion is not a worst rank). ``comparable`` is False when the name
    was not ranked by every strategy, so a smaller sum over fewer lenses is never read as
    a better one."""

    ticker: str
    display: str
    cells: dict[str, MultiStrategyCell] = field(default_factory=dict)
    rank_sum: Optional[int] = None
    graded: int = 0
    comparable: bool = True


@dataclass
class MultiStrategyResult:
    """A whole multi-lens re-grade: the per-strategy results, kept whole (each is exactly
    what a single run of that strategy produces), plus the combined ``rows`` grid and the
    run ``meta``."""

    strategy_ids: list[str]
    strategy_names: dict[str, str]
    results: dict[str, RankPipelineResult]
    rows: list[MultiStrategyRow]
    meta: dict


def combine_rank_results(results: dict[str, RankPipelineResult],
                         strategy_ids: Optional[list[str]] = None
                         ) -> list[MultiStrategyRow]:
    """The combined grid over per-strategy results — pure, so it is testable without a
    run. Ordered: names every strategy ranked first (best rank-sum first), then the
    partially-ranked ones, then the never-ranked, ties broken by ticker so the grid is
    reproducible."""
    ids = [s for s in (strategy_ids if strategy_ids is not None else list(results))
           if s in results]
    names: dict[str, str] = {}
    cells: dict[str, dict[str, MultiStrategyCell]] = {}

    def _cell(ticker: str, cell: MultiStrategyCell) -> None:
        cells.setdefault(ticker, {})[cell.strategy_id] = cell

    for sid in ids:
        res = results[sid]
        names.update(res.names or {})
        positions = cohort_positions(res.ranked)
        cohort_m = len(res.ranked)
        for r in res.ranked:
            pos, _tied = positions.get(r.ticker, (None, False))
            _cell(r.ticker, MultiStrategyCell(
                strategy_id=sid, status=_RANKED, position=pos, cohort_size=cohort_m,
                verdict=r.verdict, score=r.combined_rank))
        for status, pairs in ((_EXCLUDED, res.excluded),
                              (_UNRATEABLE, res.unrateable),
                              (_FETCH_ERROR, res.fetch_errors)):
            for ticker, reason in pairs:
                # The RAW reason is kept verbatim; the plain-English sentence rides
                # beside it (REPORT-1 wording, REPORT-2 grid), never replacing it.
                body, _flag = _split_flag(reason)
                plain = (exclusion_sentence(res, ticker, body)
                         if status == _EXCLUDED else "")
                _cell(ticker, MultiStrategyCell(
                    strategy_id=sid, status=status, reason=reason,
                    reason_plain=plain if plain != body else ""))

    rows: list[MultiStrategyRow] = []
    for ticker in sorted(cells):
        by_sid = {sid: cells[ticker].get(sid, MultiStrategyCell(strategy_id=sid))
                  for sid in ids}
        ranked_cells = [c for c in by_sid.values()
                        if c.status == _RANKED and c.position is not None]
        rows.append(MultiStrategyRow(
            ticker=ticker, display=display_name(ticker, names.get(ticker)),
            cells=by_sid,
            rank_sum=sum(c.position for c in ranked_cells) if ranked_cells else None,
            graded=len(ranked_cells),
            comparable=len(ranked_cells) == len(ids) and bool(ids)))
    rows.sort(key=lambda row: (-row.graded,
                               row.rank_sum if row.rank_sum is not None else 10**9,
                               row.ticker))
    return rows


def run_multi_strategy_pipeline(
    universe: Optional[list[str]] = None, strategy_ids: Optional[list[str]] = None, *,
    universe_id: Optional[str] = None, universes_dir: str | Path | None = None,
    strategies_dir: str | Path | None = None, adapter=None,
    today: Optional[date] = None, use_cache: bool = True,
    progress: Optional[Callable[[str], None]] = None,
    freeze_dir: str | Path | None = None,
    with_valuation_band: bool = False,
) -> MultiStrategyResult:
    """Grade ONE cohort under N rank strategies and return the combined grid (FUND-RUN-1).

    Each strategy is run through the SAME ``run_rank_pipeline`` entry the CLI and the
    Universe Run tab already use, with ``ranker_only=True`` — deterministic, free, no LLM.
    So a column here is byte-identical to that strategy's own single run, and narration
    stays a separate, optional, per-strategy choice (unchanged).

    The adapter is built ONCE and shared across the strategies, so the second lens reads
    the same cohort out of the cache instead of re-fetching it. Duplicate ids are collapsed
    (first occurrence wins); the order given is the column order.
    """
    ids: list[str] = []
    for sid in list(strategy_ids or []):
        if sid and sid not in ids:
            ids.append(sid)
    if not ids:
        raise ValueError("run_multi_strategy_pipeline needs at least one strategy id")

    today = today or date.today()
    if adapter is None:
        adapter = _build_adapter(today=today, use_cache=use_cache)

    results: dict[str, RankPipelineResult] = {}
    names: dict[str, str] = {}
    for i, sid in enumerate(ids, 1):
        if progress is not None:
            progress(f"Grading with {sid} ({i} of {len(ids)})…")
        res = run_rank_pipeline(
            list(universe) if universe else None, sid, universe_id=universe_id,
            universes_dir=universes_dir, strategies_dir=strategies_dir,
            ranker_only=True, adapter=adapter, today=today, use_cache=use_cache,
            freeze_dir=freeze_dir,
            # VALBAND-1: the band is per-NAME, not per-strategy, so compute it once (on the
            # first lens) — the shared cache means later lenses would only re-read the same
            # 5-year fetch. Every column's ranked tickers still describe the same cohort;
            # the band column is read off this first result (app renders it beside the grid).
            with_valuation_band=(with_valuation_band and i == 1))
        results[sid] = res
        names[sid] = res.meta.get("rank_strategy_name", "") or sid

    rows = combine_rank_results(results, ids)
    first = results[ids[0]]
    meta = {
        "strategy_ids": list(ids),
        "universe_id": first.meta.get("universe_id"),
        # REPORT-1/REPORT-2: the cohort's HUMAN name, carried up so the merged report's
        # single header can lead with it and keep the id beside it.
        "universe_name": first.meta.get("universe_name", ""),
        # ONE cohort under N lenses, so the membership record is the same for every column
        # (FUND-UI-2) — carried up from the first run rather than recomputed.
        "universe_members": list(first.meta.get("universe_members") or []),
        "universe_member_hash": first.meta.get("universe_member_hash", ""),
        "universe_size": first.meta.get("universe_size", 0),
        "council_mode": "ranker-only",
        "ranker_only": True,
        "graded_by_all": sum(1 for row in rows if row.comparable),
    }
    return MultiStrategyResult(strategy_ids=list(ids), strategy_names=names,
                               results=results, rows=rows, meta=meta)


# --------------------------------------------------------------------------- #
# REPORT-2 — ONE merged report per run, however many lenses ran
# --------------------------------------------------------------------------- #
VERDICT_TABLE_TITLE = "Verdict by lens"
VERDICT_TABLE_NOTE = (
    "One row per name, one column per lens. A ranked cell gives the name's position in "
    "THAT lens's cohort and its verdict; an excluded cell names the rule it failed; a "
    "name with no usable data reads \u201cno data\u201d and keeps its reason in that "
    "lens's own section below. Rank-sum adds the per-lens POSITIONS and is comparable "
    "only across names ranked by EVERY lens \u2014 \u2021 marks a sum over fewer, and "
    "nothing is imputed for an exclusion.")
_COL_RANK_SUM = "Rank-sum"
_COL_GRADED_BY = "Graded by"


def multi_strategy_columns(result: MultiStrategyResult) -> dict[str, str]:
    """``strategy_id -> column header``: the friendly display name, with the id appended
    ONLY when two selected strategies share a label (the two GARP versions do) — a column
    must never silently swallow another's cells."""
    ids = result.strategy_ids
    labels = {sid: (result.strategy_names.get(sid) or sid) for sid in ids}
    seen: dict[str, int] = {}
    for lbl in labels.values():
        seen[lbl] = seen.get(lbl, 0) + 1
    return {sid: (lbl if seen[lbl] == 1 else f"{lbl} ({sid})")
            for sid, lbl in labels.items()}


def multi_strategy_grid_rows(result: MultiStrategyResult) -> tuple[list[dict], list[str]]:
    """``(rows, columns)`` for THE verdict table — the ONE builder the Run tab, the
    merged markdown and the merged HTML all render, so the three cannot drift.

    One row per name in the combined grid's own order (best rank-sum first, then the
    partially-ranked, then the never-ranked — never re-sorted here). ``\u2021`` marks a
    rank-sum over FEWER lenses than the run used, so a smaller sum is never misread as a
    better one."""
    columns = multi_strategy_columns(result)
    head = ["Name", *columns.values(), _COL_RANK_SUM, _COL_GRADED_BY]
    rows = []
    for row in result.rows:
        rs = "—" if row.rank_sum is None else str(row.rank_sum)
        if row.rank_sum is not None and not row.comparable:
            rs = f"{rs}\u2021"
        cells = {"Name": row.display, _COL_RANK_SUM: rs,
                 _COL_GRADED_BY: f"{row.graded} of {len(result.strategy_ids)}"}
        for sid, header in columns.items():
            cells[header] = row.cells[sid].render()
        rows.append(cells)
    return rows, head


def multi_summary_line(result: MultiStrategyResult) -> str:
    """``"4 lenses × 16 names — 3 names rated BUY by every lens, 5 excluded by every
    lens, 10 of 16 ranked by at least one."``

    Derived from the combined grid every time, never hardcoded. A clause whose count is
    ZERO is omitted rather than printed — a zero is noise here, not information."""
    ids = result.strategy_ids
    n_lenses = len(ids)
    size = result.meta.get("universe_size", len(result.rows))
    buy_all = sum(1 for row in result.rows
                  if row.comparable
                  and all(c.status == _RANKED and c.verdict == "buy"
                          for c in row.cells.values()))
    excluded_all = sum(1 for row in result.rows
                       if ids and all(row.cells[s].status == _EXCLUDED for s in ids))
    ranked_any = sum(1 for row in result.rows if row.graded)
    parts = []
    if buy_all:
        parts.append(f"{buy_all} name{'s' if buy_all != 1 else ''} rated BUY by every "
                     "lens")
    if excluded_all:
        parts.append(f"{excluded_all} excluded by every lens")
    parts.append(f"{ranked_any} of {size} ranked by at least one")
    lens_word = "lens" if n_lenses == 1 else "lenses"
    return f"{n_lenses} {lens_word} × {size} names — " + ", ".join(parts)


def format_multi_strategy_grid(result: MultiStrategyResult) -> str:
    """The combined grid as text (the CLI print and the UI download read this ONE
    builder, so they cannot drift). One row per name: rank-sum, then one cell per
    strategy. A name not ranked by every strategy carries ‡ — its smaller sum is over
    fewer lenses and is NOT a better one."""
    ids = result.strategy_ids
    m = result.meta
    lines = [
        f"=== COMBINED GRID — {len(ids)} strategies over "
        f"{m.get('universe_size', 0)} name(s) in "
        f"{m.get('universe_id') or 'adhoc'} ===",
        "  Verdict: deterministic ranker (no LLM ran — narration is per-strategy).",
        "",
    ]
    head = f"  {'name':<24} {'rank-sum':<10}" + "".join(f"{sid:<34}" for sid in ids)
    lines.append(head)
    for row in result.rows:
        rs = "—" if row.rank_sum is None else str(row.rank_sum)
        if row.rank_sum is not None and not row.comparable:
            rs = f"{rs}‡"
        cells = "".join(f"{row.cells[sid].render():<34}" for sid in ids)
        lines.append(f"  {_name_col(row.display, 24):<24} {rs:<10}{cells}")
    lines.append("")
    lines.append(f"  rank-sum = sum of the per-strategy cohort POSITIONS; comparable only "
                 f"across the {m.get('graded_by_all', 0)} name(s) ranked by ALL {len(ids)} "
                 f"strategies (‡ = ranked by fewer, nothing imputed).")
    return "\n".join(lines)
