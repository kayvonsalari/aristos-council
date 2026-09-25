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

from collections import Counter

from .cleanup import Removal, SizeVerdict, clean, size_verdict
from .definitions import CohortDefinition, DefinitionError
from .freeze import (DEFINITION_FILE, MEMBERS_FILE, REMOVALS_FILE, REPORT_FILE,
                     FrozenCohort, current_version, next_version, read_members,
                     register_universe, version_dir, write_definition_snapshot,
                     write_members, write_removals)
from ..rank_engine import FactorSpec
from .quality import QualityReport, RankSetup, run_checks
from .report import render_diff, render_report
from .source import (PATH_CONSTITUENTS, PATH_INDEX, Candidate, EODHDSource, SourceProbe,
                     build_pool, build_pool_from_index, fill_missing)
from .symbols import SymbolError, yahoo_symbol

DEFAULT_ROOT = Path("data/local/cohorts")
# The COHORT-1 definitions: ten rules, own-currency floors, built from S&P 500 + STOXX 600
# constituents. Left exactly as it was so the old cohort versions can still be reproduced, which
# is what ``--constituents`` selects.
DEFAULT_DEFINITIONS = DEFAULT_ROOT / "definitions.yaml"
# COHORT-3: the list built from the market index, USD floors. The default.
DEFAULT_INDEX_DEFINITIONS = Path("data/cohort_definitions.yaml")
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
def default_index_pool():
    """The cleaned pool of the real market index, read once. No request is made."""
    from ..market_index import IndexStore, clean_pool, load_config
    from .definitions import INDEX_EXCLUDED_MARKETS

    return clean_pool(store=IndexStore(load_config()["root"]),
                      exclude_markets=INDEX_EXCLUDED_MARKETS)


def resolve_path(defn: CohortDefinition, *, constituents: bool | None = None) -> str:
    """Which source a cohort is built from, or an error that says how to get the other one.

    COHORT-3. ``constituents`` is tri-state:

    * ``None`` - as the DEFINITION says: a USD floor means the market index, none means it is a
      COHORT-1 definition and takes the constituents path it always took. This is what a caller
      that holds one definition gets.
    * ``False`` - the market index, strictly. A definition with no ``min_market_cap_usd`` is
      refused with the way out. The CLI passes this, which is what makes the index the default.
    * ``True`` - the old S&P 500 + STOXX 600 constituents, so an old cohort version can be
      reproduced. Needs the own-currency floor those definitions carry.
    """
    if constituents is True:
        if defn.min_market_cap <= 0:
            raise DefinitionError(
                f"{defn.name}: has no own-currency min_market_cap, so it cannot be built from "
                f"index constituents. Drop --constituents to build it from the market index.")
        return PATH_CONSTITUENTS
    if defn.uses_index:
        return PATH_INDEX
    if constituents is None:
        return PATH_CONSTITUENTS
    raise DefinitionError(
        f"{defn.name}: has no min_market_cap_usd, so it cannot be built from the market "
        f"index. It is a COHORT-1 definition: pass --constituents (and its own definition "
        f"file) to reproduce it from S&P 500 + STOXX 600 constituents.")


def _pool_for(defn: CohortDefinition, *, source, probe, index_pool,
              constituents: bool | None,
              progress=None):
    path = resolve_path(defn, constituents=constituents)
    if path == PATH_INDEX:
        pool = index_pool if index_pool is not None else default_index_pool()
        return build_pool_from_index(defn, pool, progress=progress)
    if source is None or probe is None:
        raise DefinitionError(f"{defn.name}: the constituents path needs a source and a probe")
    return build_pool(defn, source, probe, progress=progress)


def build(defn: CohortDefinition, *, source: EODHDSource | None = None,
          probe: SourceProbe | None = None,
          root: str | Path = DEFAULT_ROOT, universes_dir: str | Path | None = None,
          rebuild: bool = False, history_provider=None, ranker=None,
          strategy_id: str = DEFAULT_STRATEGY, today: date | None = None,
          progress=None, constituents: bool | None = None,
          index_pool=None) -> BuildOutcome:
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
    candidates, path_used, log = _pool_for(defn, source=source, probe=probe,
                                          index_pool=index_pool, constituents=constituents,
                                          progress=say)
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
    out.size = size_verdict(members, defn, _other_exchange_counts(source, defn, path_used),
                            index_path=path_used == PATH_INDEX)
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
    if path_used != PATH_CONSTITUENTS or source is None:
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
def diff(defn: CohortDefinition, *, source: EODHDSource | None = None,
         probe: SourceProbe | None = None,
         root: str | Path = DEFAULT_ROOT, history_provider=None,
         progress=None, constituents: bool | None = None,
         index_pool=None) -> tuple[str, list[Candidate], list[Candidate]]:
    """What a rebuild WOULD add or remove. Builds the pool, writes nothing."""
    root = Path(root)
    say = progress or (lambda _m: None)
    version = current_version(root, defn.slug)
    if version is None:
        return (f"{defn.name}: not built yet — nothing to diff against."), [], []

    current = {c.ticker for c in read_members(version_dir(root, defn.slug, version)
                                              / MEMBERS_FILE)}
    say(f"{defn.name}: rebuilding the pool to compare…")
    candidates, _path, _log = _pool_for(defn, source=source, probe=probe,
                                        index_pool=index_pool, constituents=constituents,
                                        progress=say)
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


# --------------------------------------------------------------------------- #
# plan - a dry run that reads the index and nothing else
# --------------------------------------------------------------------------- #
@dataclass
class PlanEntry:
    definition: CohortDefinition
    matched: int = 0                                   # names the code matched, before cleanup
    members: list[Candidate] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    size: SizeVerdict | None = None
    error: str = ""
    frozen_version: int | None = None                  # an existing cohort under this slug
    untranslatable: int = 0                            # members with no Yahoo symbol

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return self.size.status if self.size else "?"

    @property
    def markets(self) -> list[tuple[str, int]]:
        counts = Counter(m.exchange for m in self.members)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    @property
    def top(self) -> list[Candidate]:
        return sorted(self.members, key=lambda m: (-(m.market_cap_usd or 0.0), m.ticker))[:5]


def plan(defs: list[CohortDefinition], pool, *, root: str | Path = DEFAULT_ROOT
         ) -> list[PlanEntry]:
    """What each definition WOULD build from ``pool``, without building it.

    Reads a cleaned pool of the market index and nothing else: no source, no probe, no
    fundamentals call, no history provider, no ranker. The four cleanup rules run exactly as a
    build runs them - so the counts are the build's counts - except the history test, which needs
    price history and is measured at build time; it can only REMOVE names, so a cohort planned at
    the floor of the band can land under it.
    """
    entries: list[PlanEntry] = []
    for defn in defs:
        entry = PlanEntry(definition=defn, frozen_version=current_version(root, defn.slug))
        try:
            candidates, _path, _log = _pool_for(defn, source=None, probe=None, index_pool=pool,
                                                constituents=False)
        except DefinitionError as exc:
            entry.error = str(exc)
            entries.append(entry)
            continue
        entry.matched = len(candidates)
        if not candidates:
            entry.error = (f"no company in the index carries the code(s) "
                           f"{', '.join(defn.industry)}")
            entries.append(entry)
            continue
        if defn.gics_subindustry:
            carried = {c.gics_subindustry.lower() for c in candidates}
            missing = [s for s in defn.gics_subindustry if s.lower() not in carried]
            if missing:
                entry.error = (f"no company under the code(s) {', '.join(defn.industry)} carries "
                               f"the GICS sub-industry {', '.join(missing)}")
                entries.append(entry)
                continue
        members, removals = clean(candidates, defn)
        entry.members, entry.removals = members, removals
        entry.size = size_verdict(members, defn, {}, index_path=True)
        for m in members:
            try:
                yahoo_symbol(m.ticker)
            except SymbolError:
                entry.untranslatable += 1
        entries.append(entry)
    return entries


def _bn(value: float | None) -> str:
    return "?" if value is None else f"${value / 1e9:,.1f}bn"


def format_plan(entries: list[PlanEntry], pool=None) -> str:
    """The plan as text: one block per cohort, then the tally."""
    from .definitions import MAX_MEMBERS, MIN_MEMBERS

    out: list[str] = ["COHORT-3 plan - read from the market index, no network."]
    if pool is not None:
        out.extend(pool.lines()[:1])
    out.append("")
    for e in entries:
        d = e.definition
        tag = {"ok": "in band", "thin": "THIN", "wide": "WIDE", "error": "ERROR"}.get(
            e.status, e.status)
        out.append(f"{d.name}  [{tag}]  watch: {'yes' if d.watch else 'no'}")
        out.append(f"  codes:     {', '.join(d.industry)}"
                   + (f"  +  GICS sub-industry: {', '.join(d.gics_subindustry)}"
                      if d.gics_subindustry else ""))
        if e.error:
            out.append(f"  error:     {e.error}")
            out.append("")
            continue
        out.append(f"  floor:     {_bn(d.min_market_cap_usd)} USD")
        cut = len(e.removals)
        out.append(f"  members:   {len(e.members)}  ({e.matched} matched the codes; "
                   f"cleanup removed {cut})")
        out.append(f"  band:      {e.size.sentence()}")
        out.append(f"  exchanges: " + ", ".join(f"{m} {n}" for m, n in e.markets))
        out.append("  top 5:     " + "; ".join(
            f"{m.ticker} {m.name[:22]} {_bn(m.market_cap_usd)}" for m in e.top))
        notes = []
        if e.frozen_version is not None:
            notes.append(f"a cohort with this slug is already frozen at v{e.frozen_version} "
                         f"and will not be touched")
        if e.untranslatable:
            notes.append(f"{e.untranslatable} member(s) have no Yahoo symbol")
        if notes:
            out.append("  note:      " + "; ".join(notes))
        out.append("")
    tally = Counter(e.status for e in entries)
    watched_n = sum(1 for e in entries if e.definition.watch)
    out.append(f"{len(entries)} cohort(s): {tally.get('ok', 0)} in band "
               f"({MIN_MEMBERS}-{MAX_MEMBERS}), {tally.get('thin', 0)} thin, "
               f"{tally.get('wide', 0)} wide, {tally.get('error', 0)} error(s); "
               f"{watched_n} flagged watch.")
    out.append("The history test (5 years) is measured at build time and can only remove "
               "names; a cohort near the bottom of the band can land under it. Nothing is padded "
               "and nothing is truncated.")
    return "\n".join(out)
