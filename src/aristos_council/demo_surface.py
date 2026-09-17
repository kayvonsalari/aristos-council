"""Demo-surface presentation — friendly labels for a clean, professional UI.

The technical `id` (``growth_40_v1``, ``magic_formula_momentum_v1``) is the STABLE
record key: it names persisted reports, snapshot-CSV rows, and verdict history, so it is
NEVER renamed. This module decides only what the USER SEES — a friendly `display_name`
in dropdowns, with the id relegated to a small caption — so no underscores or ``_v1``
leak into the presentation while the record stays byte-stable underneath.

Pure functions (no Streamlit), so the labelling is unit-tested directly.
"""

from __future__ import annotations

# A universe is BACKSTAGE (hidden from the default demo surface, revealed by the "Show
# validation & legacy tools" toggle) when its ``role:`` marks it NEVER-GRADED — the
# observation/watch universes (energy_watch, the portfolio_watch pattern) and the
# known-trap control benches (defensive_16). FRONT-STAGE are the graded scoreboard
# universes and the exploratory lens cohorts (financials_16) — the ones you actually run
# to pick names. Role-DERIVED (UNI-1), never a hardcoded id list: a fresh universe yaml
# dropped into universes/ is classified by what its manifest declares, with zero code
# change here — the same dynamic-discovery contract 4C gave strategies.
_OBSERVATIONAL_ROLE_MARKER = "never graded"


def is_validation_universe(universe) -> bool:
    """True for a universe shown only behind the validation toggle. Role-derived: a role
    marked 'never graded' (an observation/watch universe or a known-trap control bench)
    is backstage; a missing/blank role, a graded scoreboard universe, or an exploratory
    lens cohort is front-stage. Accepts a manifest (reads ``.role``) or a bare role
    string."""
    role = universe if isinstance(universe, str) else (getattr(universe, "role", "") or "")
    return _OBSERVATIONAL_ROLE_MARKER in role.lower()


def is_hidden_strategy(strategy) -> bool:
    """True for a ``ui: hidden`` strategy (Sprint 4C — legacy/superseded configs). Hidden
    from the dropdown by default; revealed under the validation/legacy toggle. Accepts a
    loaded strategy OR a discovery ``StrategyInfo`` (a ``.hidden`` bool wins if present,
    else the ``.ui`` field)."""
    if hasattr(strategy, "ui"):                 # a loaded strategy carries the raw field
        return getattr(strategy, "ui", "") == "hidden"
    return bool(getattr(strategy, "hidden", False))    # a discovery StrategyInfo


def visible_universes(manifests, *, show_validation: bool):
    """The universe manifests a dropdown should offer: all of them when the validation
    toggle is ON, else only the non-validation (scoreboard) universes."""
    return [u for u in manifests
            if show_validation or not is_validation_universe(u)]


def visible_rank_strategies(strategies, *, show_validation: bool):
    """The rank strategies a dropdown should offer: all when the toggle is ON, else only
    the non-hidden (live) strategies. Accepts loaded strategies or StrategyInfos."""
    return [s for s in strategies
            if show_validation or not is_hidden_strategy(s)]


def suggested_first(manifests, suggested_ids):
    """Split ``manifests`` into ``(suggested, others)`` for a strategy's universe
    selectors (UNI-1). ``suggested`` are the manifests whose id is in ``suggested_ids``,
    in that DECLARED order (an id with no matching manifest is skipped); ``others`` keep
    their original order. NOTHING is dropped — every manifest lands in exactly one group,
    so a non-suggested (cross-lens) universe stays selectable. An empty/absent
    ``suggested_ids`` returns ``([], list(manifests))`` so the caller renders EXACTLY as
    before (byte-unchanged)."""
    ids = list(suggested_ids or [])
    by_id = {u.id: u for u in manifests}
    suggested = [by_id[i] for i in ids if i in by_id]
    chosen = {u.id for u in suggested}
    others = [u for u in manifests if u.id not in chosen]
    return suggested, others


def universe_label(u) -> str:
    """Friendly dropdown label for a universe manifest — its ``display_name`` if set,
    else the bare id (a graceful fallback that never crashes on an un-named manifest). A
    personal ``local=True`` list (UNIED-1) is tagged '(local)' so it reads as a saved
    custom list in every selector, front-stage."""
    base = (getattr(u, "display_name", "") or "").strip() or u.id
    return f"{base} (local)" if getattr(u, "local", False) else base


def universe_role(u) -> str:
    """Optional one-line role caption for a universe (empty when unset)."""
    return (getattr(u, "role", "") or "").strip()


def strategy_label(s) -> str:
    """Friendly dropdown label for a strategy — ``display_name`` if set, else the
    strategy's ``name``, else its id."""
    return ((getattr(s, "display_name", "") or "").strip()
            or getattr(s, "name", "") or s.id)


def strategy_role(s) -> str:
    """Optional one-line role caption for a strategy (empty when unset)."""
    return (getattr(s, "role", "") or "").strip()


# --------------------------------------------------------------------------- #
# ASSET-MODE-1 — one Stocks / ETFs switch, and the UI shows stocks by default
# --------------------------------------------------------------------------- #
# Council Station is a stock-analysis tool in daily use, and the three ETF lenses plus
# five ETF lists sat in every picker regardless. They rank on fee, size, trend and payout
# — published for every fund and genuinely useful — but they are the minority job, and a
# picker that offers them always makes the majority job slower.
#
# HIDDEN, never deleted. Past client ETF work ran on these lenses and a planned ETF-only
# list of held funds still needs them, so this is a VISIBILITY filter and nothing else:
# no strategy, universe, criterion, factor, rank, verdict, report, CLI or Colab path
# changes, and the asset-kind gate that already walls ETFs out of stock lenses is
# untouched. Flip the switch and everything is back.
STOCKS, ETFS = "Stocks", "ETFs"
ASSET_MODES = (STOCKS, ETFS)
DEFAULT_ASSET_MODE = STOCKS

# The vocabulary a Universe may declare. Deliberately the two the SWITCH offers, not the
# richer asset-kind vocabulary the gate uses: this field answers "which side of the
# switch does this list live on", which is a UI question with two answers.
ASSET_KINDS = ("stocks", "etfs")

# A lens's thesis when it is about funds, and the gate kind an ETF lens admits.
_FUNDS_THESIS = "funds"
_ETF_KIND = "etf"
_EQUITY_KIND = "equity"


def _as_list(value) -> list:
    """A field that may be a scalar, a list, or absent, as a lower-cased list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    return [str(v).strip().lower() for v in value if str(v).strip()]


def is_etf_lens(strategy) -> bool:
    """True for a lens that ranks FUNDS.

    TWO tests, because the repo carries both facts and they must agree:

    * ``asset_kinds`` contains ``etf`` and not ``equity`` — the structural test, and the
      same one the Colab script uses. It is the authoritative one: a lens is an ETF lens
      because its FACTORS read fund attributes (expense ratio, fund size), which is why
      the gate admits only funds to it.
    * ``thesis`` includes ``funds`` — the declared one.

    Either is enough. A lens that declares neither is a stock lens, and so is a lens with
    NO asset restriction at all: an unrestricted check (Forensic) reads company accounts,
    so it belongs on the stocks side even though nothing stops it being pointed at a fund.
    """
    kinds = _as_list(getattr(strategy, "asset_kinds", None))
    if _ETF_KIND in kinds and _EQUITY_KIND not in kinds:
        return True
    return _FUNDS_THESIS in _as_list(getattr(strategy, "thesis", None))


def universe_asset_kind(universe, *, etf_tickers=None) -> str:
    """``"stocks"`` or ``"etfs"`` for a list, by the first of these that decides it:

    1. the manifest's own ``asset_kind`` field. EXPLICIT ALWAYS WINS — a person who
       marked a list has answered the question, and no inference may overrule them;
    2. ``thesis: funds``;
    3. every ticker in it is a known ETF (``etf_tickers``, from the static layer and the
       cached quote types);
    4. otherwise ``stocks``.

    Step 4 is a default, not a finding: it is what an ordinary unmarked stock list gets,
    and it is also what a list lands on when nothing could decide. ``undecided_list``
    below is what tells those two apart, so the second can be warned about rather than
    quietly filed under stocks.

    NOTHING IS EVER WRITTEN BACK. The inferred value lives for the length of a render.
    """
    declared = (getattr(universe, "asset_kind", "") or "").strip().lower()
    if declared in ASSET_KINDS:
        return declared
    if _FUNDS_THESIS in _as_list(getattr(universe, "thesis", None)):
        return "etfs"
    tickers = [t.strip().upper() for t in (getattr(universe, "tickers", None) or [])
               if str(t).strip()]
    known = {t.strip().upper() for t in (etf_tickers or ())}
    if tickers and known and all(t in known for t in tickers):
        return "etfs"
    return "stocks"


def undecided_list(universe, *, etf_tickers=None) -> bool:
    """True when a list fell through to the ``stocks`` DEFAULT without anything deciding
    it — no explicit field, no thesis, and at least one ticker whose kind is unknown.

    A list of real stocks is not undecided: every one of its tickers is absent from the
    ETF set, which is a decision. This catches the list nobody can classify, so the app
    can name it once rather than file it silently."""
    if (getattr(universe, "asset_kind", "") or "").strip().lower() in ASSET_KINDS:
        return False
    if _FUNDS_THESIS in _as_list(getattr(universe, "thesis", None)):
        return False
    tickers = [t.strip().upper() for t in (getattr(universe, "tickers", None) or [])
               if str(t).strip()]
    if not tickers:
        return True                      # nothing to go on at all
    known = {t.strip().upper() for t in (etf_tickers or ())}
    hit = sum(1 for t in tickers if t in known)
    # Some funds but not all: the list is mixed, and neither side of the switch is right
    # for it. Named, and defaulted to stocks, which is where a mixed list is least wrong.
    return 0 < hit < len(tickers)


def asset_mode_filter(mode, *, etf_tickers=None):
    """``(lens_ok, list_ok)`` — the two predicates every picker that STARTS NEW ANALYSIS
    applies. ONE helper, so a tab cannot filter on its own idea of what an ETF is.

    Pure, and unaware of Streamlit: the mode is a string the caller read from wherever it
    keeps it. An unknown mode falls back to Stocks, which is the default everywhere else
    and the safe answer for a value nobody set."""
    etfs = str(mode or "").strip().lower() == "etfs"

    def lens_ok(strategy) -> bool:
        return is_etf_lens(strategy) is etfs

    def list_ok(universe) -> bool:
        return (universe_asset_kind(universe, etf_tickers=etf_tickers) == "etfs") is etfs

    return lens_ok, list_ok
