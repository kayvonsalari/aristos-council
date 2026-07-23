"""Company Check — the single-name diagnostic (Aristos v2).

Answers ONE question honestly: "why isn't X on the list?" For a single ticker under a
chosen strategy it shows every lens-screen criterion with its observed value and
pass/fail/not-evaluated state (ALL criteria evaluated for diagnosis — the universe run
short-circuits on the first confirmed fail, this does not), the sector/cap gates, each
rank factor's raw value with its position against a NAMED, DATED reference cohort, and
the price-vs-fundamentals divergence flag.

HARD CONSTRAINTS (by construction, not convention):
- **NO verdict at n=1.** A rank over a class of one is a fabricated verdict (the
  UNRATEABLE lesson in reverse). This module never emits BUY/HOLD/SELL — it reports
  criteria, values, and cohort position, and points the reader at a universe run.
- **No LLM anywhere.** Pure deterministic tools + arithmetic.
- **No fresh universe fetch.** The single name is fetched (one ticker); the cohort
  context comes from the LATEST PERSISTED (frozen) run of the reference universe,
  replayed offline — never a fresh universe pull. When no such run exists, raw values
  are shown with an explicit "(no reference run available …)".
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
class CompanyCheckResult:
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
    # VERDICT OF RECORD (Spec 4D): the checked name's verdict+rank (or exclusion+reason)
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
    """The clause quoted after "VERDICT OF RECORD:" — the checked name's outcome in the
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
# The check
# --------------------------------------------------------------------------- #
def run_company_check(
    ticker: str, rank_strategy_id: str, reference_universe_id: str, *, adapter,
    strategies_dir: str | Path | None = None, universes_dir: str | Path | None = None,
    runs_dir: str | Path | None = None, screen_strategy_id: Optional[str] = None,
    today: Optional[date] = None,
) -> CompanyCheckResult:
    """Diagnose ONE ticker under ``rank_strategy_id``'s lens screen + factors, with
    cohort context from the latest frozen run of ``reference_universe_id``. NEVER emits
    a verdict. ``adapter`` fetches the single name; tests inject a fake."""
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

    fi = gather_factor_inputs(adapter, ticker, today=today)
    f = fi.fundamentals
    company_name = getattr(f, "company_name", None) if f is not None else None

    di = DataIntegrity(
        fundamentals_ok=f is not None,
        price_ok=(fi.last_close is not None or fi.return_12m is not None),
        implausible=list(implausible_fields(f).values()))       # VERIFY-2 ITEM 4

    # UNRATEABLE — no usable data at all. Honest, no fabricated values, no verdict.
    if is_unrateable(fi):
        di.note = "no usable fundamentals or price history (possibly delisted)"
        return CompanyCheckResult(
            ticker=ticker, company_name=company_name,
            rank_strategy_id=rank_strategy.id, screen_strategy_id=screen_strategy_id_str,
            reference_universe_id=reference_universe_id, unrateable=True,
            screen=[], gates=[], factors=[], divergence_flag=None,
            reference_available=False, reference_run_id=None, reference_run_date=None,
            reference_cohort_n=0, data_integrity=di, screen_less=screen_less,
            pointer="UNRATEABLE — no data, so no diagnosis and no verdict "
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

    # FACTORS — raw value + cohort position (from the latest frozen reference run).
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
        if cohort is None:
            context = ("no reference run available — run the universe once for context"
                       if ref is None else "reference run unreadable — raw value only")
        else:
            cohort_vals = [r.factor_values.get(spec.name)
                           for r in cohort if not r.excluded]
            context = _position_phrase(value, cohort_vals, spec.resolved_direction(),
                                       reference_universe_id, ref_run_date or "?")
        factor_cells.append(FactorCell(
            factor=spec.name, label=fdef.label, value=value, source=source,
            context=context))
    di.not_evaluated_factors = not_eval_factors

    # VERDICT OF RECORD (Spec 4D) — quote the checked name's outcome VERBATIM from the
    # frozen reference run. Company Check itself NEVER issues a verdict (a rank is a cohort
    # statement); this only REPORTS one already recorded for this name in a past universe
    # run. Renders only when a frozen run exists AND the name was in it (ranked or
    # excluded); otherwise stays None and the closing boilerplate is kept.
    verdict_of_record = _verdict_of_record(
        ticker, cohort, cohort_result, cohort_n, reference_universe_id, ref_run_date)

    divergence = price_divergence_flag(fi, screen_criteria)
    pointer = _pointer(screen_cells, gates, screen_less=screen_less,
                       has_record=verdict_of_record is not None)

    return CompanyCheckResult(
        ticker=ticker, company_name=company_name,
        rank_strategy_id=rank_strategy.id, screen_strategy_id=screen_strategy_id_str,
        reference_universe_id=reference_universe_id, unrateable=False,
        screen=screen_cells, gates=gates, factors=factor_cells,
        divergence_flag=divergence, reference_available=cohort is not None,
        reference_run_id=ref_run_id, reference_run_date=ref_run_date,
        reference_cohort_n=cohort_n, data_integrity=di, pointer=pointer,
        verdict_of_record=verdict_of_record,
        market_cap_in_gates=market_cap_in_gates, screen_less=screen_less)


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


def format_company_check(result: CompanyCheckResult) -> str:
    """The text report the CLI prints — the SAME content the UI renders."""
    lines = [
        f"Company Check — {result.display} · single-name diagnostic · NO VERDICT.",
        "Verdicts are cohort statements (see docs/SCOREBOARD.md).",
        f"  strategy: {result.rank_strategy_id}  ·  lens screen: "
        f"{result.screen_strategy_id or 'none'}  ·  reference: "
        f"{result.reference_universe_id}",
        "",
    ]
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

    # VERDICT OF RECORD (Spec 4D) — quoted verbatim from the frozen run, right after the
    # factor block. Renders only when the checked name had a recorded outcome; otherwise
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
