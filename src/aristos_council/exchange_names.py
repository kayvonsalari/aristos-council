"""EXCHANGE-NAMES-1 — readable exchange names for the codes the market index stores.

The index keeps EODHD's short exchange codes ("CO", "SW", "PA", "XETRA") and US venue names
("NYSE", "NASDAQ"). A reader should never have to know that CO is Copenhagen, so every surface
that shows an exchange (find-box results, the peers table and its exports) goes through
``exchange_name``. ONE table; an unknown code is shown as itself, never blanked or guessed.
"""
from __future__ import annotations

EXCHANGE_NAMES: dict[str, str] = {
    # the 19 exchange codes MARKET-INDEX-1 covers (market_index.DEFAULT_EXCHANGES)
    "US": "US", "XETRA": "Xetra", "LSE": "London", "PA": "Euronext Paris",
    "AS": "Euronext Amsterdam", "MC": "Madrid", "MI": "Milan", "SW": "SIX Swiss",
    "ST": "Stockholm", "CO": "Copenhagen", "OL": "Oslo", "HE": "Helsinki", "TO": "Toronto",
    "HK": "Hong Kong", "KO": "Korea (KRX)", "KQ": "KOSDAQ", "AU": "ASX", "TW": "Taiwan",
    "SA": "B3 São Paulo",
    # US venues, as EODHD reports them (market_index.DEFAULT_VENUES)
    "NYSE": "NYSE", "NASDAQ": "Nasdaq", "NYSE ARCA": "NYSE Arca", "AMEX": "NYSE American",
    # other codes a row can carry
    "F": "Frankfurt", "BR": "Euronext Brussels", "LS": "Euronext Lisbon", "VI": "Vienna",
}


def exchange_name(code: str) -> str:
    """The readable name for an exchange code; the code itself when it is not in the table."""
    raw = (code or "").strip()
    if not raw:
        return ""
    return EXCHANGE_NAMES.get(raw) or EXCHANGE_NAMES.get(raw.upper()) or raw
