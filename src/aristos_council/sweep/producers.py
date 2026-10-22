"""SWEEP-1 - the real producers: exactly what the Analyse tab runs, with the real adapters.

Nothing here is a mock. A company entry calls ``run_company_report`` and a list entry calls
``run_multi_strategy_pipeline``, with the arguments the tab passes, then renders the SAME exports the
tab's download buttons serve (text, HTML, Markdown). The Markdown builders for lists live in ``app.py``
(they import streamlit), so ``app`` is imported here, as the report sweep's test does.

Two deliberate differences from a click in the browser, both so a sweep leaves the owner's records
alone: the judge panel's log rows go to a folder inside the entry (``judge_log/``) instead of the
forward-test log, and no spend-confirmation dialog stands in the way.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from ..plurals import plural
from .runner import Products
from .sets import ALL_STOCK, Entry

ROOT = Path(__file__).resolve().parents[3]
# Approximate price of ONE judge-panel model call (three families, ~5k tokens in, ~0.4k out). The
# panel does not meter its calls, so the sweep multiplies; docs/SWEEP.md says so.
APPROX_JUDGE_CALL_USD = 0.02


def _app():
    """``app.py`` (streamlit), found from the repo root wherever the sweep was started."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("app")


def stock_lenses(strategies_dir: Path):
    """``[(id, label)]`` for every lens the Analyse tab offers in Stocks mode: visible, voting AND
    check (so Forensic is ticked), in the picker's order."""
    from aristos_council.demo_surface import asset_mode_filter, strategy_label
    from aristos_council.strategy.discovery import rank_strategies
    from aristos_council.strategy.picker import strategy_choices
    from aristos_council.strategy.rank_loader import load_rank_strategy

    loaded = []
    for info in rank_strategies(strategies_dir):
        try:
            loaded.append(load_rank_strategy(info.path))
        except Exception:                                   # noqa: BLE001 - the tab skips these too
            continue
    lens_ok = asset_mode_filter("stocks")[0]
    return [(c.id, strategy_label(c.strategy)) for c in strategy_choices(loaded)
            if lens_ok(c.strategy)]


def resolve_lenses(entry: Entry, strategies_dir: Path):
    offered = stock_lenses(strategies_dir)
    if entry.lenses == ALL_STOCK:
        return offered
    labels = dict(offered)
    return [(sid, labels.get(sid, sid)) for sid in entry.lenses]


def _adapter():
    """Same as the tab's ``_company_check_adapter``: cached, on the shared on-disk day-cache."""
    from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from aristos_council.data.provider import select_market_adapter
    return CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR, today=date.today())


def produce_company(entry: Entry, flags: dict, say) -> Products:
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import format_company_report, run_company_report
    from aristos_council.data.adapter import normalize_ticker
    from aristos_council.export.report_html import company_report_html

    app = _app()
    strategies_dir, universes_dir = app.STRATEGIES_DIR, app.UNIVERSES_DIR
    lenses = resolve_lenses(entry, Path(strategies_dir))
    ticker = normalize_ticker(entry.ticker)
    cap, live = app._resolve_company_cap(ticker, match_cap_usd=None, adapter_factory=_adapter)
    include_small = bool(app.small_company_notice(cap, live_fetched=live))
    run_start = datetime.now(timezone.utc)
    judge_log = Path(flags["folder"]) / "judge_log" if flags.get("folder") else None
    report = run_company_report(
        ticker, [sid for sid, _ in lenses], adapter=_adapter(), strategies_dir=strategies_dir,
        universes_dir=universes_dir, runs_dir=ROOT / "runs", with_summary=False,
        with_council=flags["council"], with_judges=flags["judges"], include_small=include_small,
        judges_dir=judge_log, progress=say)
    p = Products(ticked=[label for _, label in lenses])
    p.names = [n for n in (report.check.company_name,) if n]
    p.objs = {"company": report}
    p.files = {"report.txt": format_company_report(report),
               "report.html": company_report_html(report, run_start=run_start),
               "report.md": company_report_markdown(report)}
    agreement = report.agreement
    facts = {"verdict": "no lens vote", "votes": "no lens voted", "council": "no"}
    if agreement is not None:
        from aristos_council.judges.panel import record_snapshot
        snap = record_snapshot(agreement)
        facts["votes"] = snap.split or "no lens voted"
        facts["verdict"] = f"{snap.cell}: {agreement.headline}"
    opinion = report.council_opinion
    if opinion is not None:
        facts["council"] = "yes" if opinion.available else f"asked, not written ({opinion.note})"
        p.calls += opinion.calls or 0
        if opinion.cost is not None:
            p.cost += float(opinion.cost)
        else:
            p.cost_note = "council cost not measured"
        p.files["council.txt"] = (opinion.narrative if opinion.available
                                  else f"No council text: {opinion.note}\n")
    panel = report.judge_panel
    facts["judge_split"] = "not asked"
    if panel is not None:
        p.judge_panel = json.loads(json.dumps(dataclasses.asdict(panel), default=str))
        p.files["judge.json"] = json.dumps(p.judge_panel, indent=2)
        facts["judge_split"] = (panel.split_line if panel.available
                                else f"panel did not run: {panel.note}")
        p.calls += panel.calls_made or 0
        p.cost += (panel.calls_made or 0) * APPROX_JUDGE_CALL_USD
        p.cost_note = (p.cost_note + "; " if p.cost_note else "") + \
            "judge cost is calls x an approximate price"
    p.facts = facts
    return p


def _universe(entry: Entry):
    """``(tickers, display name)`` as the tab resolves them: a built cohort's frozen members, or a
    pasted list checked against the local index."""
    from aristos_council import list_input as li

    if entry.cohort:
        for opt in li.cohort_options():
            if opt.slug == entry.cohort:
                return list(opt.members), opt.name
        raise LookupError(f"cohort {entry.cohort!r} is not built on this machine (the sweep never "
                          "builds one)")
    resolution = li.resolve_tickers(list(entry.tickers))
    if not resolution.send:
        raise LookupError(f"none of {list(entry.tickers)} is recognised by the local index")
    return list(resolution.send), ""


def produce_list(entry: Entry, flags: dict, say) -> Products:
    from aristos_council.export.report_html import multi_strategy_report_html
    from aristos_council.pipeline import (format_multi_strategy_grid, multi_strategy_columns,
                                          run_multi_strategy_pipeline)

    app = _app()
    lenses = resolve_lenses(entry, Path(app.STRATEGIES_DIR))
    universe, display = _universe(entry)
    run_start = datetime.now(timezone.utc)
    multi = run_multi_strategy_pipeline(
        universe, [sid for sid, _ in lenses], universe_id=None, strategies_dir=app.STRATEGIES_DIR,
        universes_dir=app.UNIVERSES_DIR, freeze_dir=ROOT / "runs", with_valuation_band=True,
        derived_from="", min_market_cap_override=None, with_reader=False, cohort_thesis="",
        progress=say)
    p = Products(ticked=list(multi_strategy_columns(multi).values()))
    p.objs = {"multi": multi}
    p.names = [getattr(r, "display", "") for r in multi.rows]
    p.files = {"report.txt": format_multi_strategy_grid(multi),
               "report.html": multi_strategy_report_html(multi, run_start=run_start),
               "report.md": app._multi_strategy_markdown(multi, run_start)}
    ag = multi.lens_agreement
    rows = []
    if ag is not None and getattr(ag, "available", False):
        rows = [{"name": r.display, "buy": len(r.buy_lenses), "sell": len(r.sell_lenses)}
                for r in ag.rows]
    p.facts = {"verdict": list_verdict_fact(len(universe), display, len(rows)),
               "votes": f"{len(multi.strategy_ids)} lenses ticked",
               "judge_split": "company pages only", "council": "no", "rows": rows}
    return p


PRODUCERS = {"company": produce_company, "list": produce_list}


def list_verdict_fact(n_names: int, display: str, n_buy: int) -> str:
    """"6 names in the list (Oil); 4 with at least one BUY" - and "1 name", never "1 names"."""
    return (f"{plural(n_names, 'name')} in the list" + (f" ({display})" if display else "")
            + f"; {n_buy} with at least one BUY")
