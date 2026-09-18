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


def yahoo_symbols(eodhd_symbols) -> list[str]:
    """Translate many, keeping order and dropping nothing silently."""
    return [yahoo_symbol(s) for s in eodhd_symbols]
