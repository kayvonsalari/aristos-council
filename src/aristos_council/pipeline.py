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
    compute_factor_outcomes,
    gather_factor_inputs,
    is_payout_uncovered,
    is_sector_excluded,
    is_sector_out_of_scope,
    is_unrateable,
    price_divergence_flag,
    screen_evaluate,
)
from .data.adapter import display_name
from .persistence.reports import RunReport, report_from_state
from .rank_engine import FactorSpec, RankedTicker, rank_universe
from .reproducibility import estimate_cost
from .state import Recommendation, ResearchState

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


def _rank_stage(universe, rank_strategy, adapter, *, today, prefilter_criteria=None):
    from .data.adapter import TransientFetchError

    rows: list[tuple[str, dict]] = []
    excluded: list[tuple[str, str]] = []
    sources_by_ticker: dict[str, dict[str, str]] = {}
    screen_bases: dict[str, dict[str, str]] = {}     # ticker -> {criterion: basis}
    abstentions_by_ticker: dict[str, dict[str, str]] = {}   # ticker -> {criterion: note}
    names_by_ticker: dict[str, str] = {}             # ticker -> company display name (ITEM 1)
    for t in universe:
        # A TRANSIENT fetch failure (429/timeout/5xx, unrecovered after retries) is NOT
        # absent data — abort THIS name with a fetch-error status (rerun), never mislabel
        # a throttled live ticker as UNRATEABLE or silently worst-rank it (ITEM 5).
        try:
            fi = gather_factor_inputs(adapter, t, today=today)
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
            reason, bases, abstentions = screen_evaluate(prefilter_criteria, fi)
            if bases:
                screen_bases[t] = bases
            if abstentions:
                abstentions_by_ticker[t] = abstentions
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
    return ranked, excluded, screen_bases, names_by_ticker


def _shortlist(ranked: list[RankedTicker], runs_on: str, k: int) -> list[RankedTicker]:
    live = [r for r in ranked if not r.excluded]
    if runs_on == "all":
        return live
    if runs_on == "top_k":
        return live[:k]                  # `ranked` is already sorted best-first
    return [r for r in live if r.verdict == "buy"]   # buy_quintile (default)


def _annotate_narration(rep: RunReport, r: RankedTicker) -> None:
    """Append rank-semantics contradiction annotations to the narrative in place (ITEM 4).

    Verifies the narrator's ordinal claims against ``r``'s authoritative rank table and
    appends `[⚠ narration check: …]` lines on a contradiction — never rewrites the prose.
    No-op when there is no rationale to check."""
    from .narration_check import check_narration
    d = getattr(rep, "decision", None)
    if d is None or not getattr(d, "rationale", ""):
        return
    table = {"N": r.universe_size, "combined_position": r.rank_position,
             "factors": dict(r.factor_ranks)}
    annotations = check_narration(d.rationale, table)
    if annotations:
        d.rationale = d.rationale.rstrip() + "\n" + "\n".join(annotations)


def _council_stage(
    shortlist: list[RankedTicker], screen_strategy, adapter, runners, mode: str, *,
    sentiment_adapter=None, sentiment_missing_key: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> list[CouncilOutcome]:
    """Run the LLM council over the shortlist (matrix skipped — the ranker is the
    verdict-of-record). Shared by ``run_pipeline`` and ``run_rank_pipeline`` so the
    per-name invocation lives in ONE place. ``progress`` (optional) is called with a
    human status string before each name — the narrator phase is minutes, so the UI
    needs a heartbeat."""
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
        result = ResearchState.model_validate(app.invoke(ResearchState(
            ticker=r.ticker, strategy_id=screen_strategy.id,
            ranker_verdict=Recommendation(r.verdict),
            ranker_explanation=r.explain(),
            ranker_cohort_size=r.universe_size,
            ranker_imputed_fraction=imputed_fraction)))
        rep = report_from_state(result)
        _annotate_narration(rep, r)                  # ITEM 4: rank-semantics post-check
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
    ranked, excluded, _, _ = _rank_stage(universe, rank_strategy, adapter, today=today,
                                          prefilter_criteria=prefilter)
    shortlist = _shortlist(ranked, runs_on, rank_strategy.k)

    outcomes = _council_stage(
        shortlist, screen_strategy, adapter, runners, mode,
        sentiment_adapter=sentiment_adapter,
        sentiment_missing_key=sentiment_missing_key)

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


def tie_boundary_notes(ranked: list[RankedTicker]) -> dict[str, str]:
    """Ticker -> ``'(=<score> — tie broken alphabetically)'`` for each ranked name that
    shares its combined score with the name ranked just ABOVE it AND received a DIFFERENT
    verdict (the tie straddles a verdict boundary; the alphabetical tie-break decided
    which side each fell on — e.g. MRK/PG both 20.0, HOLD/SELL, so PG is annotated).

    Display-only (ITEM 7): the ordering, the cut, and the verdicts are all unchanged —
    this only DISCLOSES that a boundary was decided by the tie-break."""
    notes: dict[str, str] = {}
    live = [r for r in ranked if not r.excluded]
    for prev, cur in zip(live, live[1:]):
        if cur.combined_rank == prev.combined_rank and cur.verdict != prev.verdict:
            notes[cur.ticker] = (f"(={cur.combined_rank:.1f} — "
                                 f"tie broken alphabetically)")
    return notes


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
    council_runs_on: Optional[str] = None,
    adapter=None, runners=None, today: Optional[date] = None,
    use_cache: bool = True, progress: Optional[Callable[[str], None]] = None,
    freeze_dir: str | Path | None = None, replay_run_id: Optional[str] = None,
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
    fakes instead. ``progress`` receives per-phase status strings for a live UI."""
    strategies_dir = Path(strategies_dir) if strategies_dir else _STRATEGIES_DIR
    universe, resolved_universe_id = _resolve_universe(
        universe, universe_id,
        Path(universes_dir) if universes_dir else _UNIVERSES_DIR)
    rank_strategy = load_rank_strategy_from_id(strategy_id, strategies_dir)
    screen_id = resolve_council_screen_id(rank_strategy, screen_strategy_id)
    screen_strategy = load_screen_from_id(screen_id, strategies_dir)

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
                 if getattr(rank_strategy, "prefilter_screen", False) else None)
    ranked, prerank_excluded, screen_bases, names = _rank_stage(
        universe, rank_strategy, adapter, today=today, prefilter_criteria=prefilter)
    live = [r for r in ranked if not r.excluded]
    excluded, unrateable, fetch_errors = _split_exclusions(ranked, prerank_excluded)

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
        council = _council_stage(shortlist, screen_strategy, adapter, runners, mode,
                                 progress=progress)
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
        "screen_strategy_id": screen_strategy.id,
        "universe_id": resolved_universe_id,
        "run_id": run_id,
        "council_mode": executed_mode,
        "council_runs_on": runs_on,
        "ranker_only": ranker_only,
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
        fetch_errors=fetch_errors, screen_bases=screen_bases, names=names)

    if csv_path and not ranker_only and mode != "narrator":
        _append_agreement_csv(result, Path(csv_path))
    return result


def _resolve_universe(universe, universe_id, universes_dir: Path) -> tuple[list[str], str]:
    """Turn (universe list, universe_id) into (tickers, recorded_id). A ``universe_id``
    with no explicit list loads the named manifest; an explicit list keeps its
    ``universe_id`` if given, else gets an ``adhoc:<hex8>`` fingerprint."""
    from .universe import adhoc_universe_id, load_universe_by_id
    if universe_id and not universe:
        u = load_universe_by_id(universe_id, universes_dir)
        return list(u.tickers), u.id
    if universe:
        return list(universe), (universe_id or adhoc_universe_id(list(universe)))
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


def format_factor_integrity(result: RankPipelineResult) -> list[str]:
    """The 'Factor integrity' block as text lines, e.g.
    'earnings_yield: EV 21/23 · EBIT/mcap proxy 2/23 (HD, CAT) · abstained 0'."""
    entries = factor_integrity(result)
    if not entries:
        return []
    lines = ["=== FACTOR INTEGRITY (per-factor source across the ranked names) ==="]
    for e in entries:
        lines.append(f"  {e['factor']}: " + format_integrity_entry(e))
    return lines


# Screen-criterion measurement-basis disclosure (payout-on-FCF). Payout is a SCREEN
# criterion, not a rank factor, so its basis reads across ALL screened names (ranked or
# excluded), not just the ranked ones — its own line, same format as factor integrity.
_BASIS_DISPLAY = {"fcf": "FCF (4y mean)", "eps": "EPS fallback"}


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


def format_cli_report(result: RankPipelineResult) -> str:
    """The console report the CLI prints — built from the structured result so the UI
    and CLI show the SAME thing. Mirrors the legacy run_pipeline.py layout."""
    m = result.meta
    lines = [
        f"(rank: {m['rank_strategy_id']}; screen: {m['screen_strategy_id']}; "
        f"mode: {m['council_mode']}; shortlist {len(m['shortlist'])}/"
        f"{m['universe_size']}; est ${m['est_cost']:.2f})",
        "",
        result.header,
        "",
        f"=== RANKED ({m['rank_strategy_id']}) — verdict-of-record ===",
    ]
    tie_notes = tie_boundary_notes(result.ranked)
    for i, r in enumerate(result.ranked, 1):
        disp = _disp(result, r.ticker) + ("†" if r.screen_abstentions else "")
        tie = f"  {tie_notes[r.ticker]}" if r.ticker in tie_notes else ""
        lines.append(f"  {i:>2}  {_name_col(disp):<34} {r.verdict.upper():<5} "
                     f"combined {r.combined_rank:>5.0f}{tie}")
    for foot in ranked_abstention_footnotes(result):
        lines.append(f"  {foot}")
    integrity = format_factor_integrity(result)
    if integrity:
        lines.append("")
        lines.extend(integrity)
    basis_block = format_screen_basis(result)
    if basis_block:
        lines.append("")
        lines.extend(basis_block)
    if result.excluded:
        lines.append("")
        lines.append("  Excluded (not ranked):")
        for t, reason in result.excluded:
            lines.append(f"      {_disp(result, t)} — {reason}")
    if result.unrateable:
        lines.append("")
        lines.append("  UNRATEABLE (no data — no verdict):")
        for t, reason in result.unrateable:
            lines.append(f"      {_disp(result, t)} — {reason}")
    if result.fetch_errors:
        lines.append("")
        lines.append("  FETCH FAILED — RERUN (transient; not ranked, not UNRATEABLE):")
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
