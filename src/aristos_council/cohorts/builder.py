"""COHORT-1 — build, check, diff. The orchestration, and nothing clever.

Every external dependency arrives as an argument with a lazy default: the source, the
history provider and the ranker. That is what lets the whole pipeline be tested against
recorded fixtures with no network, and it is also why this module imports neither the
pipeline nor anything that could reach a model at import time — see
``tests/test_cohorts_no_llm.py``, which asserts it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .cleanup import Removal, SizeVerdict, clean, size_verdict
from .definitions import CohortDefinition
from .freeze import (DEFINITION_FILE, MEMBERS_FILE, REMOVALS_FILE, REPORT_FILE,
                     FrozenCohort, current_version, next_version, read_members,
                     register_universe, version_dir, write_definition_snapshot,
                     write_members, write_removals)
from ..rank_engine import FactorSpec
from .quality import QualityReport, RankSetup, run_checks
from .report import render_diff, render_report
from .source import PATH_CONSTITUENTS, Candidate, EODHDSource, SourceProbe, build_pool, fill_missing
from .symbols import yahoo_symbol

DEFAULT_ROOT = Path("data/local/cohorts")
DEFAULT_DEFINITIONS = DEFAULT_ROOT / "definitions.yaml"
# The lens the quality report ranks under. Deterministic, no LLM, and already the repo's
# most opinion-free rank strategy — the report is about the COHORT, so the lens should add
# as little of its own as possible.
DEFAULT_STRATEGY = "magic_formula_raw_v1"


@dataclass
class BuildOutcome:
    definition: CohortDefinition
    version: int | None = None
    members: list[Candidate] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    size: SizeVerdict | None = None
    quality: QualityReport | None = None
    directory: Path | None = None
    universe_path: Path | None = None
    path_used: str = PATH_CONSTITUENTS
    skipped: str = ""
    frozen: bool = False
    registration_error: str = ""

    @property
    def ok(self) -> bool:
        return self.frozen

    def summary(self) -> str:
        if self.skipped:
            return f"{self.definition.name}: skipped — {self.skipped}"
        head = f"{self.definition.name}: {self.size.sentence() if self.size else '?'}"
        if not self.frozen:
            return head + " Not frozen."
        flags = len(self.quality.flags) if self.quality else 0
        return (f"{head} Frozen at v{self.version} in {self.directory}"
                + (f", {flags} check(s) flagged." if flags else ", no checks flagged."))


# --------------------------------------------------------------------------- #
# the injectable edges
# --------------------------------------------------------------------------- #
def default_history_provider(candidates: list[Candidate]) -> dict[str, float]:
    """Years of price history per EODHD symbol, from the provider the RANKER uses.

    EODHD's ``/eod`` answers 403 on this subscription, so history cannot come from the
    same place the industry does. Taking it from the ranker's own provider is the better
    answer anyway: the rule then measures the history the lenses will actually see, rather
    than a second opinion about it that could pass a name the lenses then abstain on.

    A name whose history cannot be established returns nothing rather than zero — rule 2
    skips an unknown, and rule 4 is where a name with no data is removed, with a reason
    that says so.
    """
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return {}
    from ..data.provider import select_market_adapter

    adapter = select_market_adapter("yfinance")
    today = date.today()
    start = date(today.year - 25, today.month, 1)
    out: dict[str, float] = {}
    for cand in candidates:
        try:
            history = adapter.get_price_history(yahoo_symbol(cand.ticker),
                                                start=start, end=today)
        except Exception:                      # unknown venue, no data, a bad symbol
            continue
        bars = getattr(history, "bars", None) or []
        if len(bars) < 2:
            continue
        span = (bars[-1].day - bars[0].day).days / 365.25
        out[cand.ticker] = round(span, 2)
    return out


def default_ranker(tickers: list[str], strategy_id: str, *, today: date | None = None,
                   strategies_dir: str | None = None):
    """``(ranked, unrateable, factors)`` from ONE ranker-only run. No LLM, by argument.

    The pipeline is imported HERE rather than at module scope so that importing
    ``aristos_council.cohorts`` pulls in no graph, no runner and no langchain.
    """
    from pathlib import Path as _Path

    from ..pipeline import load_rank_strategy_from_id, run_rank_pipeline

    result = run_rank_pipeline(
        tickers, strategy_id, ranker_only=True, council_mode="ranker-only",
        with_valuation_band=True, today=today, use_cache=True)
    # The SAME resolver the pipeline just used, so the factors drop-one re-ranks with are
    # by construction the factors the run ranked with. Loading the file a second way is
    # how the two quietly diverge.
    strategy = load_rank_strategy_from_id(strategy_id, _Path(strategies_dir or "strategies"))
    return result.ranked, result.unrateable, RankSetup(
        factors=tuple(FactorSpec(f.name, f.direction, f.missing) for f in strategy.factors),
        cut=strategy.cut, k=strategy.k, percentile=strategy.percentile,
        missing=strategy.missing)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(defn: CohortDefinition, *, source: EODHDSource, probe: SourceProbe,
          root: str | Path = DEFAULT_ROOT, universes_dir: str | Path | None = None,
          rebuild: bool = False, history_provider=None, ranker=None,
          strategy_id: str = DEFAULT_STRATEGY, today: date | None = None,
          progress=None) -> BuildOutcome:
    """Build one cohort from its rule, quality-check it, and freeze it if it is usable."""
    root = Path(root)
    today = today or date.today()
    say = progress or (lambda _m: None)
    out = BuildOutcome(definition=defn)

    existing = current_version(root, defn.slug)
    if existing is not None and not rebuild:
        out.skipped = (f"already built at v{existing}; pass --rebuild to cut "
                       f"v{existing + 1}")
        out.version = existing
        return out

    # 1. the pool ----------------------------------------------------------- #
    say(f"{defn.name}: building the pool…")
    candidates, path_used, log = build_pool(defn, source, probe, progress=say)
    out.path_used, out.log = path_used, log

    # 2. secondary source, for gaps only ------------------------------------ #
    log.extend(fill_missing(candidates))

    # 3. history ------------------------------------------------------------ #
    provider = history_provider if history_provider is not None else default_history_provider
    say(f"{defn.name}: measuring history for {len(candidates)} name(s)…")
    years = provider(candidates) or {}
    for cand in candidates:
        if cand.ticker in years:
            cand.history_years = years[cand.ticker]
    log.append(f"History established for {len(years)} of {len(candidates)} name(s).")

    # 4. cleanup ------------------------------------------------------------ #
    members, removals = clean(candidates, defn)
    out.members, out.removals = members, removals
    log.append(f"Cleanup removed {len(removals)}, leaving {len(members)}.")

    # 5. size --------------------------------------------------------------- #
    out.size = size_verdict(members, defn, _other_exchange_counts(source, defn, path_used))
    if not out.size.ok:
        say(f"{defn.name}: {out.size.sentence()}")
        return out

    # 6. the ranker, once, ranker-only -------------------------------------- #
    rank = ranker if ranker is not None else default_ranker
    tickers = [yahoo_symbol(c.ticker) for c in members]
    say(f"{defn.name}: ranking {len(tickers)} name(s) under {strategy_id} (no LLM)…")
    ranked, unrateable, setup = rank(tickers, strategy_id, today=today)
    version = next_version(root, defn.slug, rebuild=rebuild)
    out.quality = run_checks(
        cohort=defn.name, version=version, ranked=ranked, unrateable=unrateable,
        members=members, setup=setup, anchors=defn.anchors, notes=log)

    # 7. freeze ------------------------------------------------------------- #
    directory = version_dir(root, defn.slug, version)
    directory.mkdir(parents=True, exist_ok=True)
    write_members(directory / MEMBERS_FILE, members)
    write_definition_snapshot(directory / DEFINITION_FILE, defn, version=version,
                              built_on=today, source_path=str(root / "definitions.yaml"))
    write_removals(directory / REMOVALS_FILE, removals, log)
    (directory / REPORT_FILE).write_text(
        render_report(defn=defn, version=version, built_on=today, members=members,
                      removals=removals, size=out.size, quality=out.quality,
                      source_log=log, strategy_id=strategy_id),
        encoding="utf-8")

    out.version, out.directory, out.frozen = version, directory, True

    # 8. register ----------------------------------------------------------- #
    if universes_dir is not None:
        frozen = FrozenCohort(defn.slug, version, directory, tuple(members))
        try:
            out.universe_path = register_universe(
                frozen, defn, universes_dir=universes_dir,
                created=today.isoformat(), overwrite=rebuild)
        except ValueError as exc:                 # id collision is not a build failure
            # The cohort IS frozen; only the UI copy failed. Recorded on the outcome as
            # well as in the log, so the CLI can say it out loud rather than leaving a
            # missing list to be discovered in the sidebar.
            out.registration_error = str(exc).splitlines()[0]
            log.append(f"Universe registration skipped: {exc}")
    return out


def _other_exchange_counts(source: EODHDSource, defn: CohortDefinition,
                           path_used: str) -> dict[str, int]:
    """How many matching-industry names sit on exchanges this rule did NOT name.

    Only meaningful on the constituents path, where the whole list is in hand. On the
    screener path the honest answer is "ask the screener", so the suggestion falls back to
    widening the industry instead of naming a venue with no number behind it.
    """
    if path_used != PATH_CONSTITUENTS:
        return {}
    wanted, codes = set(defn.industry), set(defn.exchange_codes)
    counts: dict[str, int] = {}
    try:
        rows = source.constituents()
    except Exception:
        return {}
    for row in rows:
        if str(row.get("Industry") or "") in wanted:
            exchange = str(row.get("Exchange") or "")
            if exchange and exchange not in codes:
                counts[exchange] = counts.get(exchange, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def check(defn: CohortDefinition, *, root: str | Path = DEFAULT_ROOT,
          ranker=None, strategy_id: str = DEFAULT_STRATEGY,
          today: date | None = None, progress=None) -> tuple[QualityReport | None, str]:
    """Re-run the quality checks over the CURRENT frozen membership. Writes nothing."""
    root, today = Path(root), (today or date.today())
    say = progress or (lambda _m: None)
    version = current_version(root, defn.slug)
    if version is None:
        return None, f"{defn.name}: not built yet — run `build --name {defn.name}` first."

    members = read_members(version_dir(root, defn.slug, version) / MEMBERS_FILE)
    rank = ranker if ranker is not None else default_ranker
    say(f"{defn.name}: ranking {len(members)} frozen member(s) under {strategy_id}…")
    ranked, unrateable, setup = rank([yahoo_symbol(c.ticker) for c in members],
                                     strategy_id, today=today)
    report = run_checks(cohort=defn.name, version=version, ranked=ranked,
                        unrateable=unrateable, members=members, setup=setup,
                        anchors=defn.anchors)
    lines = [f"{defn.name} v{version} — {len(members)} member(s)"]
    lines += [f"  {c.name}: {c.line()}" for c in report.checks]
    return report, "\n".join(lines)


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #
def diff(defn: CohortDefinition, *, source: EODHDSource, probe: SourceProbe,
         root: str | Path = DEFAULT_ROOT, history_provider=None,
         progress=None) -> tuple[str, list[Candidate], list[Candidate]]:
    """What a rebuild WOULD add or remove. Builds the pool, writes nothing."""
    root = Path(root)
    say = progress or (lambda _m: None)
    version = current_version(root, defn.slug)
    if version is None:
        return (f"{defn.name}: not built yet — nothing to diff against."), [], []

    current = {c.ticker for c in read_members(version_dir(root, defn.slug, version)
                                              / MEMBERS_FILE)}
    say(f"{defn.name}: rebuilding the pool to compare…")
    candidates, _path, _log = build_pool(defn, source, probe, progress=say)
    fill_missing(candidates)
    provider = history_provider if history_provider is not None else default_history_provider
    years_map = provider(candidates) or {}
    for cand in candidates:
        if cand.ticker in years_map:
            cand.history_years = years_map[cand.ticker]

    members, _removals = clean(candidates, defn)
    by_ticker = {c.ticker: c for c in members}
    added = [by_ticker[t] for t in sorted(set(by_ticker) - current)]
    removed_names = sorted(current - set(by_ticker))
    removed = [Candidate(ticker=t) for t in removed_names]
    text = render_diff(defn=defn, version=version, added=added, removed=removed,
                       unchanged=len(current & set(by_ticker)))
    return text, added, removed
