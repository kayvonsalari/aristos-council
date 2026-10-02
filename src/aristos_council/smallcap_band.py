"""SMALLCAP-VIEW-1 — the opt-in peer band for a company below the $5bn lens gate.

SIZE-FLOOR-2 (78 backtests) confirmed the $5bn floor stays: lowering it helped two cohorts
and hurt five for RAW, and Growth is untestable at $5bn in most cohorts at all. So the gate
on the five voting lenses (``min_market_cap: 5.0e9`` in each lens's own YAML) is unchanged —
nothing here touches it, or any lens's math.

Instead, a company below the gate can be measured against a DIFFERENT, honestly-labelled
peer set: other companies in its OWN backtested cohort (``data/cohort_definitions.yaml``)
whose market cap sits between that cohort's own USD floor and $5bn — the band the gate
itself excludes everyone from. The lens runs its NORMAL maths over that band with the gate
switched off for the run (``pipeline.run_multi_strategy_pipeline``'s existing
``min_market_cap_override``, built for FLOOR-1/FLOOR-2's own backtest and the live Run tab's
floor control — reused here, not reimplemented); nothing about HOW a lens ranks changes,
only WHO else is in the room.

This has no track record: ``backtest.track_record`` badges are proven/promising/etc. over
the cohort the lens was BACKTESTED on, which is built and gated the SAME $5bn-and-up way the
live gate is. A band below that floor was never backtested, so Company Check skips
``attach_track_record`` for it entirely (``company_report.run_company_report``) rather than
show a badge earned on a different population.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# The lens gate every rank strategy declares (``strategies/*.yaml``'s own
# ``min_market_cap: 5.0e9``), repeated here only as the CEILING of this band — a company at
# or above it already gets a normal run and never reaches this module.
SMALLCAP_CEILING_USD = 5.0e9
MIN_ADV_USD = 3_000_000.0          # SIZE-FLOOR-1's own liquidity floor, same $3m bar
ADV_WINDOW_DAYS = 30


@dataclass
class SmallcapBand:
    """The result of trying to build the band. ``tickers`` is empty, never raises, when the
    company's industry matches no cohort, the cohort has no USD floor, or nothing in the
    band both fits the cap range and clears the liquidity floor — ``reasons`` says which."""

    tickers: list = field(default_factory=list)   # Yahoo-form, deduped, liquidity-checked
    cohort_slug: Optional[str] = None
    cohort_name: str = ""
    floor_usd: Optional[float] = None
    reasons: list = field(default_factory=list)
    skipped_no_symbol: list = field(default_factory=list)
    dropped_illiquid: list = field(default_factory=list)


def _definitions_path():
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "data" / "cohort_definitions.yaml"


def live_adv_usd(adapter, yahoo_ticker: str, *, today: date,
                 window_days: int = ADV_WINDOW_DAYS) -> Optional[float]:
    """Today's 30-day average traded value (``mean(close * volume)``), live. The historical
    equivalent (``backtest._Closes.adv_usd``) carries bisect/sorted-lookup machinery to serve
    an arbitrary PAST round date; this is always "as of today", so it is a direct, much
    smaller read of the same two fields (``PriceBar.close``, ``PriceBar.volume`` — both
    already on every adapter's bars, no new data-layer plumbing). ``None`` when the fetch
    fails or no bar carries both fields — NOT-EVAL, per rule 3: the caller drops the name
    from the band rather than claim it is liquid, but this is a BAND-MEMBERSHIP filter, not a
    verdict on the company itself, so it carries none of rule 3's null-as-false risk — a name
    this adapter cannot price is also a name the ranker could not have rated anyway."""
    try:
        history = adapter.get_price_history(
            yahoo_ticker, start=today - timedelta(days=window_days), end=today)
    except Exception:                                       # noqa: BLE001 — abstain, don't crash
        return None
    values = [b.close * b.volume for b in history.bars
              if b.close is not None and b.volume is not None]
    return (sum(values) / len(values)) if values else None


def build_smallcap_peer_band(subject, *, adapter, today: date, store=None,
                             definitions_path=None) -> SmallcapBand:
    """``subject`` is the company's own ``IndexRow`` (``report.check.peer_group.subject``) —
    the SAME row ``attach_track_record`` reads its industry/GICS off. Builds the band by
    REUSING the cohort builder's own, already-tested rules rather than re-deriving the
    industry/GICS/financials-exclusion match by hand:

      1. ``backtest.cohort_for_industry`` — which cohort this company's industry belongs to
         (pure textual match, no build required).
      2. ``cohorts.source.build_pool_from_index`` over the live, read-only index pool — the
         cohort's OWN industry/exchange filter (same code the cohort builder runs).
      3. ``cohorts.cleanup.rule_size_and_history`` — drops anyone under the cohort's OWN USD
         floor (the floor side of the band).
      4. ``cohorts.cleanup.rule_exclusions`` — financials/REITs exclusion and GICS narrowing,
         exactly as the cohort itself applies them.
      5. A ceiling filter this module adds (nothing else needs one): under $5bn.
      6. The live liquidity floor (``live_adv_usd`` >= $3m), applied last so an illiquid name
         is DROPPED from the band, never left to the ranker to discover as a fetch error.

    The subject itself is never in the returned list (callers prepend it themselves, as the
    normal peers path already does).

    ``store`` is the SAME testability hook ``run_company_report``/``attach_peers`` already
    accept (TEST-ISOLATION-1 — a test builds a tiny fake store rather than reaching the real
    on-disk market index); ``definitions_path`` likewise, for a fake cohort definitions file.
    Production code leaves both at their default (the real index, ``data/
    cohort_definitions.yaml``)."""
    from .backtest import cohort_for_industry
    from .cohorts.builder import default_index_pool
    from .cohorts.cleanup import rule_exclusions, rule_size_and_history
    from .cohorts.definitions import find_definition, load_definitions
    from .cohorts.source import build_pool_from_index
    from .cohorts.symbols import SymbolError, yahoo_symbol
    from .market_index import clean_pool
    from .cohorts.definitions import index_excluded_markets

    industry = getattr(subject, "industry", None)
    gics = getattr(subject, "gics_subindustry", None)
    subject_ticker = (getattr(subject, "ticker", "") or "").upper()

    slug = cohort_for_industry(industry, gics, definitions_path=definitions_path)
    if slug is None:
        return SmallcapBand(reasons=[
            "no backtested cohort matches this company's industry, so no small-company "
            "peer band could be built"])

    try:
        path = definitions_path or _definitions_path()
        defn = find_definition(load_definitions(path), slug)
    except Exception as exc:                                # noqa: BLE001 — abstain, don't crash
        return SmallcapBand(cohort_slug=slug, reasons=[
            f"could not load the {slug} cohort's definition: {type(exc).__name__}: {exc}"])

    if not defn.uses_index or defn.min_market_cap_usd is None:
        return SmallcapBand(cohort_slug=slug, cohort_name=defn.name, reasons=[
            f"the {defn.name} cohort has no USD floor on record, so no small-company band "
            "applies"])

    # Live, 2026-10-02: "Health - Biotechnology" is tiered to a $10bn floor (230 names at
    # that industry code — the crowdedness rule in docs/COHORTS.md puts it at the TOP tier),
    # ABOVE the $5bn ceiling every lens gates on. A company this small is below even that
    # cohort's OWN membership floor, so there is no band between the two numbers to rank it
    # in — recorded here so the empty result reads as "this cohort starts above $5bn", not
    # a silent zero. The caller still runs the normal pipeline on the company alone, which
    # the EXISTING too-few guard then reports honestly ("too few to rank (only 1 company
    # here...)") — no special-cased short-circuit needed.
    floor_reasons = []
    if defn.min_market_cap_usd >= SMALLCAP_CEILING_USD:
        floor_reasons.append(
            f"the {defn.name} cohort's own floor (${defn.min_market_cap_usd / 1e9:g}bn) is at "
            f"or above the $5bn gate, so there is no band between them for this company")

    pool = (clean_pool(store=store, exclude_markets=index_excluded_markets())
           if store is not None else default_index_pool())
    candidates, _path, _log = build_pool_from_index(defn, pool)
    candidates, _ = rule_size_and_history(candidates, defn)   # drops below the cohort's floor
    candidates, _ = rule_exclusions(candidates, defn)          # financials/REITs + GICS narrowing
    candidates = [c for c in candidates
                 if c.market_cap_usd is not None and c.market_cap_usd < SMALLCAP_CEILING_USD
                 and c.ticker.upper() != subject_ticker]

    tickers: list[str] = []
    skipped_no_symbol: list[str] = []
    for cand in candidates:
        try:
            symbol = yahoo_symbol(cand.ticker)
        except SymbolError:
            skipped_no_symbol.append(cand.ticker)
            continue
        if symbol not in tickers:
            tickers.append(symbol)

    kept, dropped_illiquid = [], []
    for symbol in tickers:
        adv = live_adv_usd(adapter, symbol, today=today)
        if adv is not None and adv >= MIN_ADV_USD:
            kept.append(symbol)
        else:
            dropped_illiquid.append(symbol)

    return SmallcapBand(tickers=kept, cohort_slug=slug, cohort_name=defn.name,
                        floor_usd=defn.min_market_cap_usd, reasons=floor_reasons,
                        skipped_no_symbol=skipped_no_symbol,
                        dropped_illiquid=dropped_illiquid)
