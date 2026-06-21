"""Market-data provider selection — one chokepoint for the composition root.

app.py and run_council.py both build the council; this is the single place that
turns the ``ARISTOS_MARKET_PROVIDER`` env var (or an explicit name) into a
concrete ``MarketDataAdapter``. Default is yfinance, so nothing changes unless a
run explicitly switches. EODHD's key requirement is enforced inside the adapter
(at first fetch), not here — selection stays cheap and side-effect-free.

Concrete adapters are imported LAZILY inside each branch: importing yfinance is
heavy, and keeping the imports out of module scope also avoids a circular import
with ``adapter`` (which the concrete adapters import).
"""

from __future__ import annotations

import os

from .adapter import MarketDataAdapter

DEFAULT_PROVIDER = "yfinance"
ENV_VAR = "ARISTOS_MARKET_PROVIDER"


def select_market_adapter(provider: str | None = None) -> MarketDataAdapter:
    """Return the configured market-data adapter.

    Resolution order: explicit ``provider`` arg, then ``$ARISTOS_MARKET_PROVIDER``,
    then the yfinance default. Unknown name -> ValueError (fail loud).
    """
    name = (provider or os.environ.get(ENV_VAR) or DEFAULT_PROVIDER).strip().lower()
    if name == "yfinance":
        from .yfinance_adapter import YFinanceAdapter
        return YFinanceAdapter()
    if name == "eodhd":
        from .eodhd_adapter import EODHDAdapter
        return EODHDAdapter()
    raise ValueError(
        f"unknown {ENV_VAR} {name!r}; known providers: yfinance, eodhd"
    )
