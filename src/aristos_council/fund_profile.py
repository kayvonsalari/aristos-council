"""Fund Profile — the single-name profile (Aristos v2), formerly "Company Check".

Answers ONE question honestly: "what is this instrument, and how does it sit against a
NAMED, VISIBLE comparison group?" For a single ticker under a chosen strategy it shows the
instrument's identity, every lens-screen criterion with its observed value and
pass/fail/not-evaluated state (ALL criteria evaluated for diagnosis — the universe run
short-circuits on the first confirmed fail, this does not), the sector/cap gates, the FULL
membership of the reference cohort, and each rank factor's raw value against that cohort's
stored median.

HARD CONSTRAINTS (by construction, not convention):
- **NO verdict at n=1.** A rank over a class of one is a fabricated verdict (the
  UNRATEABLE lesson in reverse). This module never emits BUY/HOLD/SELL — it reports
  criteria, values, and cohort position, and points the reader at a universe run.
- **No LLM anywhere.** Pure deterministic tools + arithmetic.
- **No fresh universe fetch.** The single name is fetched (one ticker); the cohort
  context comes from the LATEST PERSISTED (frozen) run of the reference universe,
  replayed offline — never a fresh universe pull. When no such run exists, raw values
  are shown with an explicit "(no reference run available …)".

FUND-PROFILE-1 added three transparency rules on top of those:
- **The cohort is never invisible.** Whenever a reference run is used, EVERY member of
  that run is listed by ticker + name with its rank and score, plus the run id and date —
  neighbours (the profiled name ±2) first. A comparison against an unnamed group is not a
  comparison, it is an assertion.
- **A rank is SCOPED.** An ordinal position is shown only when the cohort is a CONFIRMED
  fit: the profiled name is a member of the frozen run, or its declared sector matches the
  cohort's declared sector. Otherwise exactly one plain-English fit warning names the
  mismatch and the factor values render WITHOUT ordinal ranks (a clean-energy fund losing
  to US tech trackers is a category error, not a ranking).
- **Medians instead.** Each factor is shown next to the cohort's MEDIAN of that factor,
  computed from the frozen run's STORED values and labelled with the run date — shown even
  when the rank is suppressed, with the fit warning adjacent. Absolute money amounts
  (fund size) withhold the median while the cohort's currencies are unstated: a
  mixed-currency median is a wrong number, not a rough one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from .data.adapter import display_name, implausible_fields
from .factors import (
    FACTOR_REGISTRY,
    FactorInputs,
    asset_kind_display,
    compute_factor_outcomes,
    gather_factor_inputs,
    is_asset_kind_out_of_scope,
    is_borderline_fail,
    is_payout_uncovered,
    is_sector_excluded,
    is_sector_out_of_scope,
    is_unrateable,
    normalize_asset_kind,
    price_divergence_flag,
)
from .rank_engine import FactorSpec, cohort_positions, format_score

_ROOT = Path(__file__).resolve().parents[2]
_STRATEGIES_DIR = _ROOT / "strategies"
_UNIVERSES_DIR = _ROOT / "universes"
_RUNS_DIR = _ROOT / "runs"

_STATUS = {True: "PASS", False: "FAIL", None: "NOT-EVALUATED"}
# Human labels for a criterion's measurement basis (payout-on-FCF, through-cycle).
_BASIS_LABEL = {"fcf": "FCF (4y mean)", "eps": "EPS fallback", "abstained": ""}

# How many cohort neighbours are shown on each side of the profiled name before the rest of
# the table (FUND-PROFILE-1 rule 3: "the profiled name ±2").
NEIGHBOUR_WINDOW = 2

# Factors whose value is an ABSOLUTE MONEY AMOUNT rather than a ratio or a return. A median
# over these is only meaningful if every cohort value is in the same currency — and the run
# record does not state per-name currencies yet (DATA-HYGIENE-1), so the median is WITHHELD
# with its reason rather than printed as a mixed-currency number. Ratio/return factors
# (yields, expense ratio, P/B, momentum) are currency-INVARIANT and always compute, exactly
# like the currency discipline in the screening primitive (hard project rule 8).
_MONEY_DENOMINATED_FACTORS = frozenset({"fund_size"})

_MIXED_CURRENCY_NOTE = (
    "withheld — these are absolute money amounts and the run record does not state each "
    "member's currency, so a median would mix currencies (DATA-HYGIENE-1)")


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass
class ScreenCell:
    name: str
    observed: Optional[float]
    threshold: Optional[float]
    status: str                         # PASS | FAIL | NOT-EVALUATED
    basis: str = ""                     # display label ("FCF (4y mean)" / "EPS fallback")
    borderline: bool = False
    note: str = ""
    gating: bool = False                # is_gating on the strategy's CriterionSpec (4C)


@dataclass
class GateCell:
    name: str
    status: str                         # PASS | FAIL | NOT-EVALUATED
    detail: str
    rationale: str = ""                 # optional human reason, rendered after the line


@dataclass
class FactorCell:
    factor: str
    label: str
    value: Optional[float]
    source: str
    context: str                        # cohort position phrase (or the no-reference note)
    # FUND-PROFILE-1 rule 5 — the reference cohort's MEDIAN of this factor, computed from
    # the frozen run's STORED values (never a fresh fetch). None when there is no reference,
    # no comparable values, or the median is withheld (``median_note`` then says why).
    median: Optional[float] = None
    median_note: str = ""
    median_n: int = 0                   # how many stored values the median was taken over
    # False when rule 4 suppressed the ordinal rank (the cohort is not a confirmed fit).
    rank_shown: bool = True


@dataclass
class CohortMember:
    """One member of the frozen reference run, as the cohort table renders it (rule 3)."""

    ticker: str
    company_name: Optional[str]
    position: Optional[int]             # 1-based, ties SHARED (RANK-DISPLAY-1)
    tied: bool
    score: str                          # formatted combined rank-sum
    verdict: str                        # the run's recorded verdict, upper-case
    is_profiled: bool = False           # this row IS the profiled name
    neighbour: bool = False             # within ±NEIGHBOUR_WINDOW of the profiled name

    @property
    def display(self) -> str:
        return display_name(self.ticker, self.company_name)


@dataclass
class IdentityField:
    """One row of the identity header — label, an already-display-formatted value (or the
    honest absence phrase), and the provenance tag that produced it."""

    label: str
    value: str
    source: str = ""


@dataclass
class Identity:
    """What this instrument IS (rule 6) — every field carrying its provenance, and every
    absence stated rather than filled in."""

    ticker: str
    company_name: Optional[str] = None
    isin: Optional[str] = None
    isin_source: str = ""
    asset_kind: Optional[str] = None     # display form ("ETF", "Equity"); None = unknown
    asset_kind_source: str = ""
    sector: Optional[str] = None         # declared display sector; None = not assigned
    sector_source: str = ""
    fee: Optional[float] = None          # net expense ratio, a PERCENT (0.30 == 0.30%)
    fee_source: str = ""
    fund_size: Optional[float] = None
    fund_size_source: str = ""
    currency: Optional[str] = None       # listing currency, when the vendor states it
    # True for an ETF-kind name (or any name that actually has one of the fund fields):
    # a stock has no fee or fund size, so those rows are omitted rather than dashed.
    show_fund_fields: bool = False


@dataclass
class DataIntegrity:
    fundamentals_ok: bool
    price_ok: bool
    abstained_criteria: list[str] = field(default_factory=list)
    not_evaluated_factors: list[str] = field(default_factory=list)
    note: str = ""
    # Implausible vendor values flagged at the data boundary (VERIFY-2 ITEM 4) — reason
    # strings, e.g. "dividend_yield 0.2393 (>15%) — vendor value implausible — flagged".
    implausible: list[str] = field(default_factory=list)


@dataclass
class FundProfileResult:
    ticker: str
    company_name: Optional[str]
    rank_strategy_id: str
    screen_strategy_id: str
    reference_universe_id: str
    unrateable: bool
    screen: list[ScreenCell]
    gates: list[GateCell]
    factors: list[FactorCell]
    divergence_flag: Optional[str]
    reference_available: bool
    reference_run_id: Optional[str]
    reference_run_date: Optional[str]
    reference_cohort_n: int
    data_integrity: DataIntegrity
    pointer: str
    # VERDICT OF RECORD (Spec 4D): the profiled name's verdict+rank (or exclusion+reason)
    # quoted VERBATIM from the latest frozen run of the reference universe — reported
    # historical fact, NEVER recomputed from live data. The clause AFTER the label, e.g.
    # "in the latest frozen run of financials_16_v1 (run 2026-07-10): SELL, rank 12 of 16."
    # None when there is no reference, no frozen run, or the name was not in that run —
    # in which case the closing boilerplate ("a verdict requires a universe run") stays.
    verdict_of_record: Optional[str] = None
    # True when the lens-screen min_market_cap tested the SAME floor as the rank gate, so
    # market cap is printed ONCE (under GATES) and the SCREEN references it (ITEM 3).
    market_cap_in_gates: bool = False
    # True when the strategy declares NO lens screen (and none was passed): it screens
    # nothing, so the SCREEN section says so rather than diagnosing against a default lens
    # (CCFIX-2). Gates still apply.
    screen_less: bool = False
    # ---- FUND-PROFILE-1 ---------------------------------------------------- #
    identity: Optional[Identity] = None
    # The profiled name's DECLARED sector and where the declaration came from (a universe
    # manifest, the dated static layer, or the vendor). None = not assigned — never guessed.
    sector: Optional[str] = None
    sector_source: str = ""
    # The reference cohort's declared sector + friendly name, straight off its manifest.
    cohort_sector: Optional[str] = None
    cohort_display_name: str = ""
    # How the cohort FITS this name: member | sector_match | mismatch | name_sector_unknown
    # | cohort_sector_unknown | none (no reference run in play).
    cohort_fit: str = "none"
    # Ordinal ranks are rendered ONLY on a confirmed fit (rule 4).
    ranks_shown: bool = False
    # Exactly ONE plain-English sentence naming the mismatch, or None on a confirmed fit.
    fit_warning: Optional[str] = None
    # Every member of the frozen run, neighbours-first (rule 3).
    cohort_members: list[CohortMember] = field(default_factory=list)
    cohort_excluded_n: int = 0
    cohort_note: str = ""

    @property
    def display(self) -> str:
        return display_name(self.ticker, self.company_name)


# --------------------------------------------------------------------------- #
# Reference-cohort lookup (latest frozen run, replayed — NEVER a fresh fetch)
# --------------------------------------------------------------------------- #
def _latest_reference_run(runs_dir: Path, rank_strategy_id: str,
                          universe_tickers: list[str]) -> Optional[tuple[str, str]]:
    """(run_id, created) of the newest frozen run of ``rank_strategy_id`` whose frozen
    ticker set COVERS the reference universe, or None. Run ids are timestamp-prefixed,
    so reverse-lexical is newest-first."""
    if not runs_dir.exists():
        return None
    want = set(universe_tickers)
    for d in sorted((p for p in runs_dir.iterdir() if p.is_dir()),
                    key=lambda p: p.name, reverse=True):
        if not d.name.endswith(f"_{rank_strategy_id}"):
            continue
        mf = d / "manifest.json"
        if not mf.exists():
            continue
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if want and want <= set(manifest.get("tickers", [])):
            return d.name, (manifest.get("created", "") or "")
    return None


def _cohort_ranked(reference_universe_id: str, rank_strategy_id: str, run_id: str, *,
                   runs_dir: Path, universes_dir: Path, strategies_dir: Path,
                   today: date):
    """Replay the frozen run OFFLINE and return the full pipeline result (ranked names —
    each carrying factor_values — plus the excluded/unrateable partitions). No network —
    a FrozenAdapter serves the recorded inputs, so the verdicts are byte-identical to the
    original run (the VERDICT OF RECORD is that historical fact, not a live recompute)."""
    from .pipeline import run_rank_pipeline
    return run_rank_pipeline(
        None, rank_strategy_id, universe_id=reference_universe_id,
        universes_dir=universes_dir, strategies_dir=strategies_dir,
        ranker_only=True, replay_run_id=run_id, freeze_dir=runs_dir, today=today)


def _position_phrase(value: Optional[float], cohort_values: list[float], direction: str,
                     uid: str, run_date: str) -> str:
    """Where this name's factor value sits vs the reference cohort — e.g.
    'below all 23 of growth_40_v1 (run 2026-07-06)'. Higher-is-better vs
    lower-is-better handled by ``direction``."""
    if value is None:
        return "value not evaluated for this name"
    vals = [v for v in cohort_values if v is not None]
    n = len(vals)
    if n == 0:
        return f"no comparable values in {uid} (run {run_date})"
    better = sum(1 for v in vals if (v > value if direction == "high" else v < value))
    tail = f"of {uid} (run {run_date})"
    if better == n:
        return f"below all {n} {tail}"           # every cohort name is better
    if better == 0:
        return f"ahead of all {n} {tail}"
    return f"ahead of {n - better} of {n} {tail}"


def _verdict_of_record(ticker: str, cohort, cohort_result, cohort_n: int,
                       reference_universe_id: str,
                       ref_run_date: Optional[str]) -> Optional[str]:
    """The clause quoted after "VERDICT OF RECORD:" — the profiled name's outcome in the
    latest frozen reference run, VERBATIM (Spec 4D). Ranked -> verdict + position; excluded
    -> the recorded exclusion reason; not in the run (or no frozen run) -> None (the
    closing boilerplate stays). NEVER recomputed from live data — the frozen replay's
    verdicts are the original run's, reported as historical fact."""
    if cohort_result is None:
        return None
    # Ranked in the frozen run: quote the verdict-of-record + the ordinal cohort position,
    # matching the RANKED table (ties share a position, RANK-DISPLAY-1), with the combined
    # rank-SUM as detail against its best/worst bounds so it is never misread as a position.
    positions = cohort_positions(cohort)
    for r in cohort:
        if r.ticker == ticker:
            pos, tied = positions.get(ticker, (None, False))
            tie = " (tied)" if tied else ""
            best, worst = len(r.factor_ranks), len(r.factor_ranks) * cohort_n
            return (f"in the latest frozen run of {reference_universe_id} "
                    f"(run {ref_run_date}): {r.verdict.upper()}, "
                    f"rank {pos} of {cohort_n}{tie} · "
                    f"score {format_score(r.combined_rank)} "
                    f"(best {best} · worst {worst}).")
    # Excluded in the frozen run: quote the exclusion with its recorded reason.
    for t, reason in cohort_result.excluded:
        if t == ticker:
            return (f"excluded in the latest frozen run of {reference_universe_id} "
                    f"({ref_run_date}) — {reason}.")
    # In the run but UNRATEABLE (no data -> no verdict), or not in the run at all: nothing
    # to quote — no verdict of record exists for this name (house default: omit).
    return None


# --------------------------------------------------------------------------- #
# Sector declarations, cohort fit, medians (FUND-PROFILE-1)
# --------------------------------------------------------------------------- #
def _resolve_sector(ticker: str, fundamentals, *, universes_dir: Path,
                    static_row, today: date) -> tuple[Optional[str], str]:
    """The profiled name's DECLARED sector and its provenance, resolved hand-maintained
    first: universe manifests, then the dated static layer, then the vendor's own sector.

    Returns ``(sector, source)`` with ``sector=None`` whenever nothing declares one — the
    profile then says "not assigned" and shows NO sector rank (rule 4). Two manifests that
    DISAGREE resolve to None with the conflict named: an ambiguous hand-maintained claim is
    reported, never silently broken by preferring one file or falling through to the
    vendor."""
    from .etf_static import static_descriptive
    from .universe import declared_instrument_sectors

    try:
        declared = declared_instrument_sectors(universes_dir).get(ticker)
    except Exception:
        declared = None
    if declared is not None:
        return declared.sector, declared.source

    desc = static_descriptive(static_row, today)
    if desc.sector:
        return desc.sector, desc.tag
    if desc.stale_note:
        return None, f"static layer: {desc.stale_note}"

    vendor = getattr(fundamentals, "sector", None) if fundamentals is not None else None
    if vendor:
        return vendor, "vendor: sector"
    return None, ""


def cohort_fit(*, is_member: bool, name_sector: Optional[str],
               cohort_sector: Optional[str]) -> str:
    """How well the reference cohort fits this name (rule 4) — CONFIRMED-ONLY, exactly like
    every other gate in this codebase:

    - ``member`` — the name is IN the frozen run, so the cohort is its own group by
      construction and the recorded rank is a fact about it.
    - ``sector_match`` — both sides declare a sector and they are the same.
    - ``mismatch`` — both declare a sector and they differ.
    - ``name_sector_unknown`` / ``cohort_sector_unknown`` — a side declares nothing, so no
      match can be CONFIRMED. Absent data never manufactures a fit.
    """
    from .universe import same_sector

    if is_member:
        return "member"
    if not name_sector:
        return "name_sector_unknown"
    if not cohort_sector:
        return "cohort_sector_unknown"
    return "sector_match" if same_sector(name_sector, cohort_sector) else "mismatch"


def fit_warning(fit: str, *, ticker: str, name_sector: Optional[str],
                cohort_label: str, cohort_sector: Optional[str]) -> Optional[str]:
    """EXACTLY ONE plain-English sentence naming the mismatch, or None on a confirmed fit.

    The reader must be able to see why the positions were withheld without knowing
    anything about the codebase — the failure this replaces is a clean-energy UCITS fund
    shown losing to US tech trackers with no explanation."""
    if fit in ("member", "sector_match", "none"):
        return None
    tail = "the factor values below are rough context, not a ranking"
    if fit == "mismatch":
        return (f"Reference group is {cohort_label} (declared sector: {cohort_sector}) "
                f"and does not contain {ticker} (sector: {name_sector}) — {tail}.")
    if fit == "name_sector_unknown":
        return (f"{ticker} has no assigned sector, so it cannot be matched to the "
                f"reference group {cohort_label}, which does not contain it — {tail}.")
    return (f"Reference group {cohort_label} declares no sector and does not contain "
            f"{ticker}, so the two cannot be matched — {tail}.")


def median(values) -> Optional[float]:
    """The median of the PRESENT values (None entries dropped — a NOT-EVALUATED value is
    not a zero). None when nothing is left to take a median of. Even counts average the two
    middle values, as a median does."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return (float(vals[mid - 1]) + float(vals[mid])) / 2.0


def cohort_median(factor: str, values) -> tuple[Optional[float], int, str]:
    """``(median, n, note)`` for one factor over the cohort's STORED values.

    An absolute money amount withholds the median while the run record does not state each
    member's currency (``_MONEY_DENOMINATED_FACTORS``) — a mixed-currency median is a wrong
    number, not a rough one. Everything else is a ratio or a return and is
    currency-invariant, so it computes."""
    vals = [v for v in values if v is not None]
    if factor in _MONEY_DENOMINATED_FACTORS:
        return None, len(vals), _MIXED_CURRENCY_NOTE
    if not vals:
        return None, 0, "no stored values for this factor in the reference run"
    return median(vals), len(vals), ""


def _cohort_members(cohort, names: dict, profiled: str) -> tuple[list[CohortMember], str]:
    """EVERY ranked member of the frozen run as display rows (rule 3), ordered
    NEIGHBOURS-FIRST: the profiled name ±``NEIGHBOUR_WINDOW`` as a block (in rank order),
    then the remaining members in rank order. When the profiled name is not a member there
    is no window and the whole cohort renders in rank order.

    Also returns the ordering note the surfaces print under the table."""
    positions = cohort_positions(cohort)
    ordered = sorted(cohort, key=lambda r: r.combined_rank)
    rows = []
    for r in ordered:
        pos, tied = positions.get(r.ticker, (None, False))
        rows.append(CohortMember(
            ticker=r.ticker, company_name=names.get(r.ticker),
            position=pos, tied=tied, score=format_score(r.combined_rank),
            verdict=(r.verdict or "").upper(), is_profiled=(r.ticker == profiled)))
    ix = next((i for i, m in enumerate(rows) if m.is_profiled), None)
    if ix is None:
        return rows, "full cohort in rank order"
    lo = max(0, ix - NEIGHBOUR_WINDOW)
    hi = min(len(rows), ix + NEIGHBOUR_WINDOW + 1)
    window = rows[lo:hi]
    for m in window:
        m.neighbour = True
    rest = rows[:lo] + rows[hi:]
    return window + rest, (f"neighbours first (this name ±{NEIGHBOUR_WINDOW}), then the "
                           "rest in rank order")


# --------------------------------------------------------------------------- #
# The profile
# --------------------------------------------------------------------------- #
def run_fund_profile(
    ticker: str, rank_strategy_id: str, reference_universe_id: str, *, adapter,
    strategies_dir: str | Path | None = None, universes_dir: str | Path | None = None,
    runs_dir: str | Path | None = None, screen_strategy_id: Optional[str] = None,
    today: Optional[date] = None, static_rows=None,
) -> FundProfileResult:
    """Profile ONE ticker under ``rank_strategy_id``'s lens screen + factors, with cohort
    context from the latest frozen run of ``reference_universe_id``. NEVER emits a verdict.
    ``adapter`` fetches the single name; tests inject a fake. ``static_rows`` overrides the
    committed ETF static layer (tests inject rows; production loads the CSV)."""
    from .pipeline import load_rank_strategy_from_id, load_screen_from_id
    from .tools.criteria.registry import Evidence, run_screen

    strategies_dir = Path(strategies_dir) if strategies_dir else _STRATEGIES_DIR
    universes_dir = Path(universes_dir) if universes_dir else _UNIVERSES_DIR
    runs_dir = Path(runs_dir) if runs_dir else _RUNS_DIR
    today = today or date.today()

    rank_strategy = load_rank_strategy_from_id(rank_strategy_id, strategies_dir)
    # CCFIX-2: resolve the lens WITHOUT the blunt default. A strategy that declares no
    # council_screen_strategy (and none passed explicitly) screens NOTHING — quality
    # enters via ranking only; do not diagnose it against a default (growth) lens. (The
    # council path keeps resolve_council_screen_id's default; that is out of scope here.)
    resolved_screen_id = screen_strategy_id or rank_strategy.council_screen_strategy
    screen_less = not resolved_screen_id
    screen_strategy = (None if screen_less
                       else load_screen_from_id(resolved_screen_id, strategies_dir))
    screen_criteria = list(screen_strategy.criteria) if screen_strategy else []
    screen_strategy_id_str = screen_strategy.id if screen_strategy else ""

    fi = gather_factor_inputs(adapter, ticker, today=today, static_rows=static_rows)
    f = fi.fundamentals
    company_name = getattr(f, "company_name", None) if f is not None else None
    static_row = _static_row(ticker, static_rows)

    di = DataIntegrity(
        fundamentals_ok=f is not None,
        price_ok=(fi.last_close is not None or fi.return_12m is not None),
        implausible=list(implausible_fields(f).values()))       # VERIFY-2 ITEM 4

    # IDENTITY (rule 6) — what this instrument IS, resolved for every shape incl.
    # UNRATEABLE, so a no-data name still says what was asked for and what is missing.
    sector, sector_source = _resolve_sector(
        ticker, f, universes_dir=universes_dir, static_row=static_row, today=today)
    identity = _identity(ticker, company_name, fi, static_row=static_row, today=today,
                         sector=sector, sector_source=sector_source)

    # UNRATEABLE — no usable data at all. Honest, no fabricated values, no verdict.
    if is_unrateable(fi):
        di.note = "no usable fundamentals or price history (possibly delisted)"
        return FundProfileResult(
            ticker=ticker, company_name=company_name,
            rank_strategy_id=rank_strategy.id, screen_strategy_id=screen_strategy_id_str,
            reference_universe_id=reference_universe_id, unrateable=True,
            screen=[], gates=[], factors=[], divergence_flag=None,
            reference_available=False, reference_run_id=None, reference_run_date=None,
            reference_cohort_n=0, data_integrity=di, screen_less=screen_less,
            identity=identity, sector=sector, sector_source=sector_source,
            pointer="UNRATEABLE — no data, so no profile and no verdict "
                    "(a SELL would imply an assessment that cannot be made here).")

    # SCREEN — evaluate EVERY criterion (no short-circuit). Evidence built exactly as the
    # rank stage builds it (dividends=[]), so this table matches the universe screen. A
    # screen-less strategy (CCFIX-2) has no criteria — the screen stays empty.
    screen_cells: list[ScreenCell] = []
    abstained: list[str] = []
    if not screen_less:
        ev = Evidence(fundamentals=f, last_close=fi.last_close,
                      return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[])
        screen_result = run_screen(screen_criteria, ev, ticker=ticker)
        for c in screen_result.criteria:
            basis = getattr(c, "basis", "") or ""
            if c.passed is None:
                abstained.append(c.name)
            # GATING is the flag the screen RUNNER actually enforces (CCFIX-3): the
            # prefilter EXCLUDES on any confirmed fail (passed is False) but NEVER on an
            # abstention (passed is None). So an evaluated criterion is GATING (a fail
            # would/does exclude); a criterion renders non-gating ONLY when it abstains,
            # i.e. the runner genuinely would not exclude on it. NOT the disposition-
            # ceiling is_gating flag (which the prefilter ignores).
            screen_cells.append(ScreenCell(
                name=c.name, observed=c.observed, threshold=c.threshold,
                status=_STATUS[c.passed], basis=_BASIS_LABEL.get(basis, basis),
                borderline=(c.passed is False
                            and is_borderline_fail(c.observed, c.threshold)),
                note=c.note, gating=c.passed is not None))
    di.abstained_criteria = abstained

    # GATES — sector / market-cap / coarse payout (rank-strategy universe filters).
    gates = _gate_cells(rank_strategy, f)

    # ITEM 3: market cap is printed ONCE (under GATES). When the lens-screen
    # min_market_cap criterion tests the SAME floor as the gate, it is a duplicate —
    # drop it from the SCREEN table and reference GATES. A DIFFERENT floor is a genuinely
    # distinct constraint (e.g. a 5B lens floor over a 1B universe gate) and stays.
    market_cap_in_gates = False
    mcap_gate = getattr(rank_strategy, "min_market_cap", None)
    if mcap_gate is not None:
        dupe = next((c for c in screen_cells
                     if c.name == "min_market_cap" and c.threshold == mcap_gate), None)
        if dupe is not None:
            screen_cells = [c for c in screen_cells if c is not dupe]
            market_cap_in_gates = True

    # FACTORS — raw value + cohort context (from the latest frozen reference run).
    reference_universe = _load_universe(reference_universe_id, universes_dir)
    ref = _latest_reference_run(runs_dir, rank_strategy.id, _universe_tickers(
        reference_universe_id, universes_dir))
    cohort_result = None
    cohort = None
    ref_run_id = ref_run_date = None
    cohort_n = 0
    if ref is not None:
        ref_run_id, created = ref
        ref_run_date = (created[:10] if created else ref_run_id[:10])
        try:
            cohort_result = _cohort_ranked(
                reference_universe_id, rank_strategy.id, ref_run_id,
                runs_dir=runs_dir, universes_dir=universes_dir,
                strategies_dir=strategies_dir, today=today)
            cohort = [r for r in cohort_result.ranked if not r.excluded]
            cohort_n = len(cohort)
        except Exception:
            cohort_result = None
            cohort = None                       # replay failed -> fall back to raw values

    # COHORT TRANSPARENCY + SCOPED RANKING (rules 3 + 4). The cohort is listed in full
    # whenever it is used; the ordinal ranks are shown only on a CONFIRMED fit.
    cohort_sector = (reference_universe.sector or None
                     if reference_universe is not None else None)
    cohort_display = (_universe_label(reference_universe)
                      if reference_universe is not None else reference_universe_id)
    cohort_members: list[CohortMember] = []
    cohort_order_note = ""
    fit = "none"
    warning = None
    if cohort is not None:
        is_member = any(r.ticker == ticker for r in cohort)
        fit = cohort_fit(is_member=is_member, name_sector=sector,
                         cohort_sector=cohort_sector)
        warning = fit_warning(fit, ticker=ticker, name_sector=sector,
                              cohort_label=cohort_display, cohort_sector=cohort_sector)
        cohort_members, cohort_order_note = _cohort_members(
            cohort, getattr(cohort_result, "names", {}) or {}, ticker)
    ranks_shown = fit in ("member", "sector_match")

    factor_specs = [FactorSpec(fac.name, fac.direction, fac.missing)
                    for fac in rank_strategy.factors]
    outcomes = compute_factor_outcomes(fi, [s.name for s in factor_specs])
    factor_cells: list[FactorCell] = []
    not_eval_factors: list[str] = []
    for spec in factor_specs:
        value, source = outcomes[spec.name]
        if value is None:
            not_eval_factors.append(spec.name)
        fdef = FACTOR_REGISTRY[spec.name]
        med: Optional[float] = None
        med_n = 0
        med_note = ""
        if cohort is None:
            context = ("no reference run available — run the universe once for context"
                       if ref is None else "reference run unreadable — raw value only")
        else:
            cohort_vals = [r.factor_values.get(spec.name)
                           for r in cohort if not r.excluded]
            med, med_n, med_note = cohort_median(spec.name, cohort_vals)
            if ranks_shown:
                context = _position_phrase(value, cohort_vals, spec.resolved_direction(),
                                           reference_universe_id, ref_run_date or "?")
            else:
                # Rule 4: no ordinal rank against a cohort that is not a confirmed fit.
                context = ("rank not shown — the reference group is not a confirmed "
                           "sector match (see FIT)")
        factor_cells.append(FactorCell(
            factor=spec.name, label=fdef.label, value=value, source=source,
            context=context, median=med, median_n=med_n, median_note=med_note,
            rank_shown=ranks_shown and cohort is not None))
    di.not_evaluated_factors = not_eval_factors

    # VERDICT OF RECORD (Spec 4D) — quote the profiled name's outcome VERBATIM from the
    # frozen reference run. Fund Profile itself NEVER issues a verdict (a rank is a cohort
    # statement); this only REPORTS one already recorded for this name in a past universe
    # run. Renders only when a frozen run exists AND the name was in it (ranked or
    # excluded); otherwise stays None and the closing boilerplate is kept.
    verdict_of_record = _verdict_of_record(
        ticker, cohort, cohort_result, cohort_n, reference_universe_id, ref_run_date)

    divergence = price_divergence_flag(fi, screen_criteria)
    pointer = _pointer(screen_cells, gates, screen_less=screen_less,
                       has_record=verdict_of_record is not None)

    return FundProfileResult(
        ticker=ticker, company_name=company_name,
        rank_strategy_id=rank_strategy.id, screen_strategy_id=screen_strategy_id_str,
        reference_universe_id=reference_universe_id, unrateable=False,
        screen=screen_cells, gates=gates, factors=factor_cells,
        divergence_flag=divergence, reference_available=cohort is not None,
        reference_run_id=ref_run_id, reference_run_date=ref_run_date,
        reference_cohort_n=cohort_n, data_integrity=di, pointer=pointer,
        verdict_of_record=verdict_of_record,
        market_cap_in_gates=market_cap_in_gates, screen_less=screen_less,
        identity=identity, sector=sector, sector_source=sector_source,
        cohort_sector=cohort_sector, cohort_display_name=cohort_display,
        cohort_fit=fit, ranks_shown=ranks_shown and cohort is not None,
        fit_warning=warning, cohort_members=cohort_members,
        cohort_excluded_n=(len(cohort_result.excluded) if cohort_result is not None else 0),
        cohort_note=_cohort_note(ref_run_id if cohort_members else None,
                                 cohort_order_note))


def _cohort_note(run_id: Optional[str], order_note: str) -> str:
    """The line under the cohort table: how it is ordered, and where the authoritative
    full table lives (rule 3 — "full table in run record")."""
    if not run_id:
        return ""
    parts = [p for p in (order_note, f"full table in the run record: runs/{run_id}") if p]
    return "; ".join(parts)


def _static_row(ticker: str, static_rows):
    """The committed static row for this ticker (or an injected one in tests). None when
    the layer has nothing — a stock, or an unlisted fund."""
    from .etf_static import default_static_rows
    rows = static_rows if static_rows is not None else default_static_rows()
    try:
        return rows.get(ticker)
    except Exception:
        return None


def _identity(ticker: str, company_name: Optional[str], fi: FactorInputs, *,
              static_row, today: date, sector: Optional[str],
              sector_source: str) -> Identity:
    """The identity header (rule 6). Every field carries its provenance; every absent field
    stays absent (no invented ISIN, sector, or fee)."""
    from .etf_static import static_descriptive

    f = fi.fundamentals
    quote_type = getattr(f, "quote_type", None) if f is not None else None
    kind = normalize_asset_kind(quote_type)
    desc = static_descriptive(static_row, today)
    fee = getattr(f, "net_expense_ratio", None) if f is not None else None
    size = getattr(f, "total_assets", None) if f is not None else None
    static = fi.static
    fee_source = _field_provenance(static, "net_expense_ratio", fee)
    size_source = _field_provenance(static, "total_assets", size)
    return Identity(
        ticker=ticker, company_name=company_name,
        isin=desc.isin, isin_source=(desc.tag if desc.isin else ""),
        asset_kind=(asset_kind_display(quote_type) or None),
        asset_kind_source=("vendor: quoteType" if kind else ""),
        sector=sector, sector_source=sector_source,
        fee=fee, fee_source=fee_source,
        fund_size=size, fund_size_source=size_source,
        currency=(getattr(f, "currency", None) if f is not None else None),
        show_fund_fields=(kind == "etf" or fee is not None or size is not None))


def _field_provenance(static, fund_field: str, value) -> str:
    """The provenance tag for an identity number: the static layer's dated receipt when it
    filled (or withheld) the field, else the generic vendor/absent statement — the SAME
    receipt convention the factor source badges use."""
    if static is not None:
        if fund_field in getattr(static, "filled", {}):
            return static.filled[fund_field]
        if fund_field in getattr(static, "stale", {}):
            return static.stale[fund_field]
    return "vendor" if value is not None else ""


def _universe_label(u) -> str:
    """The cohort's friendly name for the fit warning — display_name when the manifest
    declares one, else the id (never invented)."""
    return (getattr(u, "display_name", "") or "").strip() or u.id


def _load_universe(universe_id: str, universes_dir: Path):
    from .universe import load_universe_by_id
    if not universe_id:
        return None
    try:
        return load_universe_by_id(universe_id, universes_dir)
    except Exception:
        return None


def _universe_tickers(universe_id: str, universes_dir: Path) -> list[str]:
    from .universe import load_universe_by_id
    try:
        return list(load_universe_by_id(universe_id, universes_dir).tickers)
    except Exception:
        return []


def _gate_cells(rank_strategy, f) -> list[GateCell]:
    """The rank-strategy universe gates as PASS/FAIL/NOT-EVALUATED rows (confirmed-only,
    exactly like the pipeline: a missing input NOT-EVALUATES, never silently excludes)."""
    gates: list[GateCell] = []
    # Asset-kind scope (ETF-1 ITEM 2) — fires FIRST in the pipeline, so it leads here.
    # Confirmed-only: unknown quoteType NOT-EVALUATES, an out-of-scope kind FAILs with the
    # scope message, an in-scope kind PASSes. Rendered like the sector-scope rationale.
    kinds = getattr(rank_strategy, "asset_kinds", []) or []
    if kinds:
        qt = getattr(f, "quote_type", None) if f else None
        if qt is None:
            gates.append(GateCell("asset_kind", "NOT-EVALUATED",
                                  f"asset kind unknown; scope is {', '.join(kinds)}"))
        elif is_asset_kind_out_of_scope(qt, kinds):
            gates.append(GateCell(
                "asset_kind", "FAIL",
                f"asset kind '{asset_kind_display(qt)}' outside this strategy's scope",
                rationale=getattr(rank_strategy, "asset_kind_rationale", "") or ""))
        else:
            gates.append(GateCell("asset_kind", "PASS",
                                  f"asset kind '{asset_kind_display(qt)}' within scope"))
    # Sector exclusion.
    sectors = getattr(rank_strategy, "exclude_sectors", []) or []
    if sectors:
        sector = getattr(f, "sector", None) if f else None
        if sector is None:
            gates.append(GateCell("sector", "NOT-EVALUATED",
                                  f"sector unknown; excludes {', '.join(sectors)}"))
        elif is_sector_excluded(sector, sectors):
            # The optional rationale comes ONLY from strategy config — never hardcoded
            # here (ITEM 2). Empty -> the gate line renders bare, as before.
            gates.append(GateCell(
                "sector", "FAIL",
                f"sector '{sector}' is excluded by this strategy",
                rationale=getattr(rank_strategy, "sector_exclusion_rationale", "") or ""))
        else:
            gates.append(GateCell("sector", "PASS",
                                  f"sector '{sector}' not excluded"))
    # Sector INCLUSION scope (FIN-1) — mirror of the exclusion gate. Confirmed-only:
    # unknown sector NOT-EVALUATES, an out-of-scope sector FAILs with the scope message,
    # an in-scope sector PASSes. Rendered exactly like the exclusion rationale.
    include = getattr(rank_strategy, "include_sectors", []) or []
    if include:
        sector = getattr(f, "sector", None) if f else None
        if sector is None:
            gates.append(GateCell("sector_scope", "NOT-EVALUATED",
                                  f"sector unknown; scope is {', '.join(include)}"))
        elif is_sector_out_of_scope(sector, include):
            gates.append(GateCell(
                "sector_scope", "FAIL",
                f"sector '{sector}' outside this strategy's scope",
                rationale=getattr(rank_strategy, "sector_inclusion_rationale", "") or ""))
        else:
            gates.append(GateCell("sector_scope", "PASS",
                                  f"sector '{sector}' within scope"))
    # Minimum market cap.
    floor = getattr(rank_strategy, "min_market_cap", None)
    if floor is not None:
        cap = getattr(f, "market_cap", None) if f else None
        if cap is None:
            gates.append(GateCell("min_market_cap", "NOT-EVALUATED",
                                  f"market cap unknown vs floor {floor:,.0f}"))
        else:
            ok = cap >= floor
            gates.append(GateCell("min_market_cap", "PASS" if ok else "FAIL",
                                  f"market cap {cap:,.0f} vs floor {floor:,.0f}"))
    # Coarse payout gate.
    max_payout = getattr(rank_strategy, "max_payout_ratio", None)
    if max_payout is not None:
        pr = getattr(f, "payout_ratio", None) if f else None
        if pr is None:
            gates.append(GateCell("payout", "NOT-EVALUATED",
                                  f"payout unknown vs ceiling {max_payout:.0%}"))
        elif is_payout_uncovered(pr, max_payout):
            gates.append(GateCell("payout", "FAIL",
                                  f"payout {pr:.0%} > ceiling {max_payout:.0%}"))
        else:
            gates.append(GateCell("payout", "PASS",
                                  f"payout {pr:.0%} <= ceiling {max_payout:.0%}"))
    return gates


# The closing tail for a name that would appear on a universe list. Normally it says a
# verdict requires a universe run; when a VERDICT OF RECORD is quoted above (Spec 4D), the
# name ALREADY has one from a past run, so the boilerplate is REPLACED with a pointer to
# it. Either way the sacred rule holds: a rank is a cohort statement, never issued LIVE for
# one name.
_NO_RECORD_TAIL = ("a verdict requires a universe run (a rank is a cohort statement, "
                   "never issued for one name)")
_RECORD_TAIL = ("see the VERDICT OF RECORD above, quoted from that frozen run (a rank is "
                "a cohort statement, never issued live for one name)")


def _pointer(screen: list[ScreenCell], gates: list[GateCell],
             screen_less: bool = False, has_record: bool = False) -> str:
    """The closing pointer — never a verdict. Names the confirmed fails that would keep
    the name OFF a universe list, or says it passes and points at a universe run. A
    screen-less strategy (CCFIX-2) never claims a SCREEN exclusion — only the gates can
    exclude, and it points that out explicitly. When ``has_record`` (a VERDICT OF RECORD
    was quoted above, Spec 4D), the "a verdict requires a universe run" boilerplate is
    swapped for a pointer to that quoted record."""
    tail = _RECORD_TAIL if has_record else _NO_RECORD_TAIL
    gate_fails = [g.name for g in gates if g.status == "FAIL"]
    if screen_less:
        if gate_fails:
            return ("Would be EXCLUDED from a universe list (a GATE fail, NOT a SELL) on: "
                    + ", ".join(gate_fails) + ". This strategy screens nothing — quality "
                    "enters via ranking; a rank/verdict is a cohort statement, so run the "
                    "universe to place it.")
        return ("This strategy screens nothing (quality enters via ranking) and passes "
                f"the sector/cap gates — {tail}.")
    fails = [c.name for c in screen if c.status == "FAIL"] + gate_fails
    if fails:
        return ("Would be EXCLUDED from a universe list (a screen fail, NOT a SELL) on: "
                + ", ".join(fails) + ". A rank/verdict is a cohort statement — run the "
                "universe to place it.")
    return f"Passes the screen — {tail}."


# --------------------------------------------------------------------------- #
# Asset-kind scoping for selectors (rule 7)
# --------------------------------------------------------------------------- #
def detect_asset_kind(adapter, ticker: str) -> Optional[str]:
    """The vendor's NORMALIZED asset kind for one ticker ("etf" / "equity" / …), or None
    when it cannot be detected. NEVER raises: a detection failure must widen a selector to
    the full list, not break it or silently narrow it to the wrong asset class."""
    if not ticker:
        return None
    try:
        f = adapter.get_fundamentals(ticker)
    except Exception:
        return None
    if f is None:
        return None
    return normalize_asset_kind(getattr(f, "quote_type", None))


def strategies_for_asset_kind(strategies, kind: Optional[str]) -> list:
    """The strategies whose declared ``asset_kinds`` admit ``kind`` (rule 7) — an ETF
    ticker offers ETF strategies, an equity ticker equity ones.

    A strategy that declares NO asset_kinds scopes nothing and is always admitted. A
    ``kind`` of None (detection failed) returns the FULL list — never a guess about which
    asset class the reader meant. If the filter would empty the list, the full list is
    returned too: a dead dropdown is worse than a wide one."""
    items = list(strategies)
    if not kind:
        return items
    out = [s for s in items
           if not (getattr(s, "asset_kinds", None) or [])
           or kind in {k.strip().lower() for k in s.asset_kinds}]
    return out or items


# --------------------------------------------------------------------------- #
# Text formatting (CLI + markdown-ish; the UI renders the structured result directly)
# --------------------------------------------------------------------------- #
def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and (abs(v) >= 1e6 or (v != 0 and abs(v) < 1e-3)):
        return f"{v:,.0f}"
    return f"{v:.4g}"


# Factors whose value is a return and reads as a SIGNED PERCENT (+711%), matching the
# divergence flag — never the raw ratio 7.11 (ITEM 3).
_PERCENT_FACTORS = frozenset({"momentum_12m", "momentum_6m"})


def format_factor_value(factor: str, value: Optional[float]) -> str:
    """A factor's value for display: momentum as a signed percent (+711%, consistent
    with the divergence flag), everything else via the general number formatter."""
    if value is None:
        return "—"
    if factor in _PERCENT_FACTORS:
        return f"{value:+.0%}"
    return _fmt_num(value)


def _expense_ratio_gloss(value: Optional[float]) -> str:
    """Plain-English gloss for the ETF expense-ratio line (ETFCHK-2): the raw ratio reads
    as a bare number, so spell out what it costs — the fund's annual fee in euros per
    €1,000 held. Empty when the value is absent (nothing to gloss). Leading em-dash so it
    appends onto the value in the line.

    UNIT ASSUMPTION (ETFCHK-3): the vendor ``net_expense_ratio`` is a PERCENT, not a
    fraction — SCHD's 0.06% expense ratio arrives as ``0.06`` (see the ETF coverage probe,
    reports/exploratory/etf_coverage_probe_2026-07-15.md). So the annual fee per €1,000 is
    ``(value / 100) × €1,000 == value × 10`` (0.06 -> €0.60, 0.61 -> €6.10). The earlier
    ``value × €1,000`` treated it as a fraction and read 100× too high (0.06 -> €60.00)."""
    if value is None:
        return ""
    per_1000 = value * 10   # (value percent / 100) × €1,000 held
    return (f" — the fund's annual fee: €{per_1000:.2f} per €1,000 held, "
            "charged every year")


def fee_display(fee: Optional[float]) -> str:
    """The identity header's fee cell. The expense ratio is a RATIO, so it carries no
    currency of its own — stated explicitly, because rule 6 requires every money figure in
    this header to say what currency it is in and a ratio's honest answer is "none"."""
    if fee is None:
        return "not available"
    return f"{fee:.2f}% per year — a ratio, so currency-invariant"


def fund_size_display(size: Optional[float], currency: Optional[str],
                      source: str = "") -> str:
    """The identity header's fund-size cell — an ABSOLUTE money amount, so it never renders
    without saying what currency it is in (rule 6).

    A static-layer value is denominated in the FUND's reporting currency, which the static
    row does not record; saying so is the honest rendering (and the reason the cohort
    median for fund size is withheld until DATA-HYGIENE-1 lands)."""
    if size is None:
        return "not available"
    amount = f"{size:,.0f}"
    if source.startswith("static:"):
        listing = f"; listing currency is {currency}" if currency else ""
        return (f"{amount} — reporting currency not recorded on the static row{listing}")
    if currency:
        return f"{amount} {currency}"
    return f"{amount} — currency not stated"


def identity_rows(identity: Optional[Identity]) -> list[IdentityField]:
    """The identity header as label/value/source rows — the ONE builder every surface
    (text report, HTML export, Streamlit tab) renders, so the three cannot drift."""
    if identity is None:
        return []
    rows = [
        IdentityField("name", display_name(identity.ticker, identity.company_name)),
        IdentityField("ticker", identity.ticker),
        IdentityField("ISIN", identity.isin or "not known", identity.isin_source),
        IdentityField("asset kind", identity.asset_kind or "not detected",
                      identity.asset_kind_source),
        IdentityField("sector", identity.sector or "not assigned",
                      identity.sector_source),
    ]
    if identity.show_fund_fields:
        rows.append(IdentityField("fee (expense ratio)", fee_display(identity.fee),
                                  identity.fee_source))
        rows.append(IdentityField(
            "fund size",
            fund_size_display(identity.fund_size, identity.currency,
                              identity.fund_size_source),
            identity.fund_size_source))
    return rows


def format_median(cell: FactorCell, run_date: Optional[str]) -> str:
    """The median clause for one factor (rule 5) — always labelled with the run date, and
    stating WHY when the median is withheld. Empty when there is no reference cohort."""
    if cell.median is not None:
        plural = "" if cell.median_n == 1 else "s"
        return (f"cohort median {format_factor_value(cell.factor, cell.median)} "
                f"over {cell.median_n} stored value{plural} (run {run_date or '?'})")
    if cell.median_note:
        return f"cohort median {cell.median_note} (run {run_date or '?'})"
    return ""


def cohort_member_line(m: CohortMember) -> str:
    """One cohort-table row for the text report: the marker, the shared-tie ordinal, the
    ticker + name, the run's verdict, and the score."""
    mark = "→" if m.is_profiled else " "
    pos = f"#{m.position}" if m.position is not None else "—"
    tie = " (tied)" if m.tied else ""
    return (f"  {mark} {pos:<5}{tie:<8} {m.ticker:<10} {m.display:<34} "
            f"{m.verdict:<5} score {m.score}")


def format_fund_profile(result: FundProfileResult) -> str:
    """The text report the CLI prints — the SAME content the UI renders."""
    lines = [
        f"Fund Profile — {result.display} · single-name profile · NO VERDICT.",
        "Verdicts are cohort statements (see docs/SCOREBOARD.md).",
        f"  strategy: {result.rank_strategy_id}  ·  lens screen: "
        f"{result.screen_strategy_id or 'none'}  ·  reference: "
        f"{result.reference_universe_id}",
        "",
    ]
    # IDENTITY (rule 6) — what this instrument is, with provenance on every field.
    rows = identity_rows(result.identity)
    if rows:
        lines.append("IDENTITY:")
        for r in rows:
            tag = f"  [{r.source}]" if r.source else ""
            label = f"{r.label}:"
            lines.append(f"  {label:<22} {r.value}{tag}")
        lines.append("")

    if result.unrateable:
        lines.append(f"UNRATEABLE — {result.data_integrity.note}")
        lines.append(result.pointer)
        return "\n".join(lines)

    if result.screen_less:
        lines.append("SCREEN: no lens screen — this strategy screens nothing; quality "
                     "enters via ranking only. Gates below still apply.")
    else:
        lines.append("SCREEN (all criteria evaluated for diagnosis; universe runs exclude "
                     "on first confirmed fail):")
        for c in result.screen:
            tags = ["gating" if c.gating else "non-gating"]
            if c.basis:
                tags.append(c.basis)
            if c.borderline:
                tags.append("borderline")
            tag = f"  [{'; '.join(tags)}]"
            if c.status == "FAIL" and c.observed is None:
                # A must-fail with no observed value (e.g. PEG growth <= 0 — undefined,
                # fails closed by design): render its REASON, not a bare "— vs threshold".
                reason = c.note or "fails closed by design"
                lines.append(f"  {c.status:<14} {c.name:<26} {reason}{tag}")
            else:
                lines.append(f"  {c.status:<14} {c.name:<26} observed "
                             f"{_fmt_num(c.observed)} vs threshold "
                             f"{_fmt_num(c.threshold)}{tag}")
        if result.market_cap_in_gates:
            lines.append("  (min_market_cap — same floor as the universe gate; shown "
                         "once, under GATES below)")
    if result.gates:
        lines.append("")
        lines.append("GATES (sector / cap / payout):")
        for g in result.gates:
            lines.append(f"  {g.status:<14} {g.name:<26} {g.detail}")
            if g.rationale:
                lines.append(f"                 ↳ {g.rationale}")

    # REFERENCE COHORT (rule 3) — every member, by ticker + name, with rank and score.
    if result.cohort_members:
        lines.append("")
        lines.append(f"REFERENCE COHORT — {result.cohort_display_name} "
                     f"({result.reference_universe_id} · run {result.reference_run_date} "
                     f"· run id {result.reference_run_id} · "
                     f"{result.reference_cohort_n} ranked, "
                     f"{result.cohort_excluded_n} excluded · declared sector: "
                     f"{result.cohort_sector or 'none declared'}):")
        if result.cohort_note:
            lines.append(f"  {result.cohort_note}.")
        has_window = any(m.neighbour for m in result.cohort_members)
        rest_marked = False
        for m in result.cohort_members:
            if has_window and not m.neighbour and not rest_marked:
                lines.append("  — remaining members, rank order —")
                rest_marked = True
            lines.append(cohort_member_line(m))

    # FIT (rule 4) — exactly one sentence when the cohort is not a confirmed match.
    if result.fit_warning:
        lines.append("")
        lines.append(f"FIT: {result.fit_warning}")

    lines.append("")
    ref = (f"reference: latest run of {result.reference_universe_id} "
           f"(run {result.reference_run_date}, {result.reference_cohort_n} ranked)"
           if result.reference_available
           else "reference: none available — run the universe once for context")
    lines.append(f"FACTOR VALUES + CONTEXT ({ref}):")
    for fc in result.factors:
        # ETFCHK-2: gloss the expense-ratio line with its plain-English per-€1,000 fee;
        # the basis tag ([source]) and cohort context (— context) are untouched. Empty
        # for every other factor, so stock-strategy output stays byte-for-byte identical.
        gloss = _expense_ratio_gloss(fc.value) if fc.factor == "expense_ratio" else ""
        lines.append(f"  {fc.label} ({fc.factor}): "
                     f"{format_factor_value(fc.factor, fc.value)}{gloss} "
                     f"[{fc.source}] — {fc.context}")
        med = format_median(fc, result.reference_run_date)
        if med:
            lines.append(f"    ↳ {med}")

    # VERDICT OF RECORD (Spec 4D) — quoted verbatim from the frozen run, right after the
    # factor block. Renders only when the profiled name had a recorded outcome; otherwise
    # nothing here and the closing boilerplate keeps "a verdict requires a universe run".
    if result.verdict_of_record:
        lines.append(f"VERDICT OF RECORD: {result.verdict_of_record}")

    if result.divergence_flag:
        lines.append("")
        lines.append(f"DIVERGENCE: {result.divergence_flag}")

    di = result.data_integrity
    lines.append("")
    lines.append("DATA INTEGRITY:")
    lines.append(f"  fundamentals: {'ok' if di.fundamentals_ok else 'MISSING'}  ·  "
                 f"price: {'ok' if di.price_ok else 'MISSING'}")
    if di.abstained_criteria:
        lines.append(f"  criteria not evaluated (abstained): "
                     f"{', '.join(di.abstained_criteria)}")
    if di.not_evaluated_factors:
        lines.append(f"  factors not evaluated: {', '.join(di.not_evaluated_factors)}")
    for flag in di.implausible:                                # VERIFY-2 ITEM 4
        lines.append(f"  ⚠ {flag}")

    lines.append("")
    lines.append(result.pointer)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Backwards-compatible internal ids (FUND-PROFILE-1 rename)
# --------------------------------------------------------------------------- #
# The FEATURE is user-visibly "Fund Profile" everywhere. These aliases keep the older
# INTERNAL ids importable so saved snapshots, the acceptance script, and any external
# caller keep working without a migration — user-visible strings are all renamed.
CompanyCheckResult = FundProfileResult
run_company_check = run_fund_profile
format_company_check = format_fund_profile
