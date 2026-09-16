"""Diagnose the valuation band end to end. Run from the repo root:
       python diagnose_band.py
Prints exactly where the band dies. Nothing is swallowed."""
import datetime as _dt
import traceback

from aristos_council.pipeline import _build_adapter
from aristos_council.tools.valuation_band import BAND_YEARS, valuation_band
from aristos_council.data.cache import ADAPTER_SCHEMA_VERSION, DEFAULT_CACHE_DIR

TICKERS = ["PFE", "JNJ", "VZ"]
today = _dt.date.today()
window_days = round(365.25 * BAND_YEARS) + 10

print(f"cache schema version : {ADAPTER_SCHEMA_VERSION}   (4 = the fix is present)")
print(f"cache dir            : {DEFAULT_CACHE_DIR}")
print(f"band window requested: {window_days} days (~{BAND_YEARS}y)\n")

adapter = _build_adapter(today=today, use_cache=True)

for t in TICKERS:
    print(f"--- {t} " + "-" * 50)
    # 1. the SHORT window the ranking legs use
    try:
        p400 = adapter.get_price_history(t, start=today - _dt.timedelta(days=400), end=today)
        b400 = getattr(p400, "bars", None) or []
        print(f"  400d fetch : {len(b400)} bars")
    except Exception as e:
        print(f"  400d fetch : FAILED -> {type(e).__name__}: {e}")

    # 2. the LONG window the band needs -- the one that used to collide
    try:
        p5y = adapter.get_price_history(t, start=today - _dt.timedelta(days=window_days), end=today)
        b5y = getattr(p5y, "bars", None) or []
        if b5y:
            span = (b5y[-1].day - b5y[0].day).days / 365.25
            print(f"  5y  fetch  : {len(b5y)} bars, span {span:.2f}y  "
                  f"({b5y[0].day} -> {b5y[-1].day})")
            if span < 4.5:
                print("               ^^ STILL SHORT — the window is not reaching the provider")
        else:
            print("  5y  fetch  : returned ZERO bars")
    except Exception as e:
        print(f"  5y  fetch  : FAILED -> {type(e).__name__}: {e}")
        traceback.print_exc()
        b5y = []

    # 3. the band itself, with the real error surfaced
    try:
        f = adapter.get_fundamentals(t)
    except Exception as e:
        print(f"  fundamentals: FAILED -> {type(e).__name__}: {e}")
        continue
    if not b5y:
        print("  band       : cannot compute (no bars)")
        continue
    try:
        print(f"  band       : {valuation_band(b5y, f, asof=today)}")
    except Exception as e:
        print(f"  band       : RAISED -> {type(e).__name__}: {e}")
        traceback.print_exc()
    print()