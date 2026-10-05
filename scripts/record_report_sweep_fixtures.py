"""REPORT-SWEEP-1 - record the offline fixtures the report sweep replays.

Run ONCE, by a person, with network (yfinance is free; no key and no model is used):

    python -m scripts.record_report_sweep_fixtures

It runs the awkward-company set through the real report code against the live adapter, captures
every raw payload that was fetched (fundamentals, prices, dividends, consensus) and writes them,
together with the small market-index slice each company page ranked against, under
``tests/fixtures/report_sweep/``. After that the sweep (``tests/test_report_sweep.py``) replays
them with no network at all, which is what lets it run in CI.

The awkward set:  F (loss-maker)  VKTX (pre-revenue biotech)  NVCR.US (small cap)
1211.HK (foreign reporting currency)  JPM (bank)  an ETF list (SPY, SCHD, VWRL.L, AAPL)  and stock
lists of 1, 3 and 6 names.

Refreshing it is a deliberate act: the committed files are what the sweep pins, so a refresh is a
reviewed change to the fixtures, never an automatic one.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "report_sweep"
PEERS_PER_COMPANY = 14       # the ladder needs 12+ comparable names; a few spare

COMPANIES = ["F", "VKTX", "NVCR.US", "1211.HK", "JPM"]
SMALL = {"VKTX", "NVCR.US"}
STOCK_LENSES = ["conservative_plus_v1", "cyclical_income_v1", "magic_formula_momentum_v1",
                "growth_garp_v2", "magic_formula_raw_v1", "financials_v1", "forensic_v1",
                "quality_v1", "epv_v1"]
LIST_LENSES = ["magic_formula_raw_v1", "quality_v1", "magic_formula_momentum_v1"]
CAR_LIST = ["F", "GM", "TM", "STLA", "HMC", "TSLA"]
ETF_LIST = ["SPY", "SCHD", "VWRL.L", "AAPL"]
ETF_LENSES = ["etf_dividend_v1", "etf_core_v1"]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from aristos_council.company_report import run_company_report
    from aristos_council.market_index import IndexStore, load_config, peers
    from aristos_council.persistence.replay import RecordingAdapter, freeze_run
    from aristos_council.pipeline import (_build_adapter, run_multi_strategy_pipeline,
                                          run_rank_pipeline)

    class LongestPrices(RecordingAdapter):
        """Keep the LONGEST price series per ticker: the 5-year valuation-band fetch and the
        400-day factor fetch hit the same key, and replay filters by the window asked for."""

        def get_price_history(self, ticker, *, start, end):
            obj = self._inner.get_price_history(ticker, start=start, end=end)
            from aristos_council.data.cache import _ser_prices
            old = self._rec(ticker).get("prices")
            if old is None or len(obj.bars) > len(old.get("bars", old)):
                self._rec(ticker)["prices"] = _ser_prices(obj)
            return obj

    today = date.today()
    real_store = IndexStore(load_config()["root"])
    live = _build_adapter(today=today, use_cache=False)
    recorder = LongestPrices(live)

    index_rows: dict[str, list] = {}
    for ticker in COMPANIES:
        group = peers(ticker, store=real_store)
        if group.subject is None:
            print(f"skip {ticker}: not in the market index")
            continue
        import math
        cap = group.subject.market_cap_usd or 1.0
        closest = sorted((m for m in group.members if m.market_cap_usd),
                         key=lambda m: abs(math.log(m.market_cap_usd / cap)))
        rows = [group.subject] + closest[:PEERS_PER_COMPANY]
        index_rows[ticker] = [dataclasses.asdict(r) for r in rows]

    from aristos_council.market_index import IndexRow

    class Store:
        def __init__(self, rows):
            self._rows = rows

        def load(self):
            return list(self._rows)

    def noop_news(ticker, *, today):
        from aristos_council.data.news_fallback import NewsFetchResult
        return NewsFetchResult(items=(), source="", tried=("sweep fixtures: no news recorded",))

    for ticker, dicts in index_rows.items():
        store = Store([IndexRow(**d) for d in dicts])
        print("company report:", ticker, f"({len(dicts) - 1} peers)")
        try:
            run_company_report(ticker, STOCK_LENSES, adapter=recorder, store=store, today=today,
                               include_small=ticker in SMALL, save=False, news_fetcher=noop_news,
                               runs_dir=OUT / "_scratch_runs")
        except Exception as exc:                     # noqa: BLE001 - record what we can
            print("  failed:", type(exc).__name__, exc)

    for size in (1, 3, 6):
        names = CAR_LIST[:size]
        print("stock list:", names)
        run_multi_strategy_pipeline(names, LIST_LENSES, adapter=recorder, today=today,
                                    use_cache=False)
        run_rank_pipeline(names, "magic_formula_raw_v1", ranker_only=True, adapter=recorder,
                          today=today, use_cache=False)
    print("etf list:", ETF_LIST)
    run_multi_strategy_pipeline(ETF_LIST, ETF_LENSES, adapter=recorder, today=today,
                                use_cache=False)
    run_rank_pipeline(ETF_LIST, "etf_dividend_v1", ranker_only=True, adapter=recorder,
                      today=today, use_cache=False)

    frozen = OUT / "frozen"
    if frozen.exists():
        import shutil
        shutil.rmtree(frozen)
    freeze_run(recorder, run_id="frozen", runs_dir=OUT, created=today.isoformat())
    (OUT / "index_rows.json").write_text(json.dumps(index_rows, indent=1), encoding="utf-8")
    (OUT / "meta.json").write_text(json.dumps({
        "recorded_on": today.isoformat(), "companies": COMPANIES, "small": sorted(SMALL),
        "stock_lenses": STOCK_LENSES, "list_lenses": LIST_LENSES, "car_list": CAR_LIST,
        "etf_list": ETF_LIST, "etf_lenses": ETF_LENSES}, indent=1), encoding="utf-8")
    import shutil
    shutil.rmtree(OUT / "_scratch_runs", ignore_errors=True)
    print("tickers recorded:", len(recorder.records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
