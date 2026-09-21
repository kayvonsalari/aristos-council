"""COHORT-1 — EODHD symbol to the symbol the rest of Aristos ranks.

The builder discovers names in EODHD's notation (``SHEL.LSE``) and the ranker fetches them
in Yahoo's (``SHEL.L``). One translation table, in one place, tested against real pairs —
because a cohort that freezes symbols the ranker cannot resolve is a cohort of UNRATEABLE
rows, and that failure would look like a data problem rather than a spelling one.

members.csv keeps BOTH: the EODHD symbol is what the definition matched and what a rebuild
compares against, the Yahoo symbol is what every consumer runs.
"""
from __future__ import annotations

# EODHD exchange code -> Yahoo suffix. "" means Yahoo uses the bare code (US).
EODHD_TO_YAHOO_SUFFIX: dict[str, str] = {
    "US": "", "NYSE": "", "NASDAQ": "", "BATS": "", "AMEX": "", "NYSE MKT": "",
    "LSE": ".L", "IL": ".L",
    "XETRA": ".DE", "F": ".F", "STU": ".SG", "BE": ".BE", "HM": ".HM", "MU": ".MU",
    "PA": ".PA", "AS": ".AS", "BR": ".BR", "LS": ".LS", "IR": ".IR",
    "SW": ".SW", "VX": ".SW",
    "ST": ".ST", "CO": ".CO", "OL": ".OL", "HE": ".HE",
    "MI": ".MI", "MC": ".MC", "VI": ".VI", "WAR": ".WA",
    # MARKET-INDEX-1 — the venues the market index tracks beyond Europe. Same table,
    # extended rather than a second one: a symbol translated two different ways in two
    # places is how a cohort and a peer group end up disagreeing about the same company.
    "TO": ".TO", "V": ".V", "NEO": ".NE",          # Canada
    "HK": ".HK", "SHG": ".SS", "SHE": ".SZ",       # Hong Kong / mainland China
    "T": ".T",                                      # Tokyo
    "KO": ".KS", "KS": ".KS", "KQ": ".KQ",         # Korea
    "AU": ".AX", "ASX": ".AX", "NZ": ".NZ",        # Australia / New Zealand
    "TW": ".TW", "TWO": ".TWO",                    # Taiwan
    "SA": ".SA",                                    # Brazil
    "NSE": ".NS", "BSE": ".BO",                    # India
    "JSE": ".JO", "TA": ".TA", "IS": ".IS",        # South Africa / Israel / Turkey
    "SR": ".SR", "MX": ".MX",                      # Saudi / Mexico
}


class SymbolError(ValueError):
    """An exchange this repo has no Yahoo translation for."""


def yahoo_symbol(eodhd_symbol: str) -> str:
    """``"SHEL.LSE"`` -> ``"SHEL.L"``; ``"XOM.US"`` -> ``"XOM"``.

    A dot inside the CODE (Yahoo's ``BRK.B``, EODHD's ``BRK-B``) is normalised to the
    hyphen Yahoo actually serves, because the naive split would otherwise read the class
    letter as an exchange.
    """
    raw = (eodhd_symbol or "").strip()
    if not raw:
        raise SymbolError("empty symbol")
    if "." not in raw:
        return raw.upper()
    code, _, exchange = raw.rpartition(".")
    exchange = exchange.upper()
    if exchange not in EODHD_TO_YAHOO_SUFFIX:
        raise SymbolError(
            f"no Yahoo translation for exchange {exchange!r} (symbol {raw!r}). Add it to "
            f"cohorts.symbols.EODHD_TO_YAHOO_SUFFIX rather than guessing at the call site.")
    return code.upper().replace(".", "-") + EODHD_TO_YAHOO_SUFFIX[exchange]


# ABS-READINGS-3 - the other direction. A Yahoo symbol is what the rest of Aristos holds;
# EODHD needs its own. Built from the same table so the two can never disagree: the first
# EODHD code that maps to a suffix wins, which is the canonical one for that venue.
_YAHOO_TO_EODHD: dict[str, str] = {}
for _code, _suffix in EODHD_TO_YAHOO_SUFFIX.items():
    _YAHOO_TO_EODHD.setdefault(_suffix, _code)


def eodhd_symbol(yahoo_ticker: str) -> str:
    """``"KO"`` -> ``"KO.US"``; ``"SHEL.L"`` -> ``"SHEL.LSE"``.

    A bare symbol is a US listing, which is the convention EODHD itself uses. A suffix it
    does not know raises, rather than a guess being sent to the provider.
    """
    raw = (yahoo_ticker or "").strip().upper()
    if not raw:
        raise SymbolError("empty symbol")
    if "." not in raw:
        return f"{raw}.US"
    code, _, suffix = raw.rpartition(".")
    key = f".{suffix}"
    if key not in _YAHOO_TO_EODHD:
        raise SymbolError(
            f"no EODHD translation for the Yahoo suffix {key!r} (symbol {raw!r})")
    return f"{code}.{_YAHOO_TO_EODHD[key]}"


def yahoo_symbols(eodhd_symbols) -> list[str]:
    """Translate many, keeping order and dropping nothing silently."""
    return [yahoo_symbol(s) for s in eodhd_symbols]
