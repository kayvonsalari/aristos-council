# KO FCF probe — prints Coca-Cola's annual free-cash-flow series as the adapter sees it,
# plus dividends paid, so the through-cycle payout arithmetic can be checked by hand.
# Run from the repo root:  python ko_fcf_probe.py
import os, json, sys
sys.path.insert(0, "src")
os.environ.setdefault("ARISTOS_MARKET_PROVIDER", "hybrid")

adapter = None
for modname, factory in [
    ("aristos_council.data.factory", "get_market_adapter"),
    ("aristos_council.data.adapter", "get_market_adapter"),
    ("aristos_council.data.hybrid_adapter", "HybridAdapter"),
    ("aristos_council.data.yfinance_adapter", "YFinanceAdapter"),
]:
    try:
        mod = __import__(modname, fromlist=[factory])
        obj = getattr(mod, factory)
        adapter = obj() if callable(obj) else obj
        print(f"[adapter via {modname}.{factory}]")
        break
    except Exception:
        continue
if adapter is None:
    print("Could not construct an adapter — paste this output to C."); sys.exit(1)

f = adapter.get_fundamentals("KO")
print("\n=== every cash/dividend/fcf-ish field on Fundamentals ===")
for name in dir(f):
    if name.startswith("_"): continue
    low = name.lower()
    if any(k in low for k in ("fcf", "cash", "dividend", "payout", "capex", "operating")):
        try:
            val = getattr(f, name)
            if not callable(val):
                print(f"{name} = {val}")
        except Exception as e:
            print(f"{name} -> error {e}")
print("\nPaste EVERYTHING above back to C (desktop copy-paste or screenshot).")
