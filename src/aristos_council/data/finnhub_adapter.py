"""Finnhub implementation of SentimentAdapter (free tier).

Endpoints used — both available on the free tier as of mid-2026:
- /company-news            recent headlines for a symbol
- /stock/recommendation    monthly analyst buy/hold/sell aggregates

Key handling: read from FINNHUB_API_KEY env var (or constructor). Free tier is
rate-limited (~60 calls/min) — far above our 2 calls per council run, but a
nightly multi-ticker watchlist should still space its runs.

Uses urllib from the stdlib on purpose: two simple GET requests don't justify
another dependency.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from .sentiment import (
    NewsItem,
    RecommendationTrend,
    SentimentAdapter,
    SentimentDataUnavailable,
)

_BASE = "https://finnhub.io/api/v1"


# --------------------------------------------------------------------------- #
# FINNHUB-SKIP-1 — the plan is US-only, so a non-US symbol is not requested at all
# --------------------------------------------------------------------------- #
# Colab check, 2026-09-18, production key: every endpoint (quote, profile, company-news,
# recommendation, peers) returns 200 for FTI and 403 for SBMO.AS and AKSO.OL. The plan is
# US-only for ALL data, not just news.
#
# The oil services run made five calls per non-US name to rediscover that, and then put
# "HTTP 403" in the dark-channel table — which reads like an outage. It is not an outage;
# it is a subscription boundary, and a boundary should be stated, not probed.
NON_US_SUFFIX = re.compile(r"\.[A-Za-z]{1,4}$")
NON_US_ENV = "FINNHUB_NON_US"


def is_non_us_symbol(ticker: str) -> bool:
    """A symbol carrying an exchange suffix (.AS, .OL, .MI, .HK, .T, .TO, .L, .PA, ...).

    Suffix-shaped rather than a list of known venues: a list would be one more thing to
    keep current, and every venue Finnhub cannot serve looks like this. US symbols carry
    no suffix, which is exactly the distinction the plan draws.
    """
    return bool(NON_US_SUFFIX.search((ticker or "").strip()))


def non_us_reason(ticker: str) -> str:
    """What the dark-channel table says instead of "HTTP 403"."""
    return (f"Finnhub data is US-only on the current plan; not requested for "
            f"{(ticker or '').strip()}")


def finnhub_non_us_enabled() -> bool:
    """``FINNHUB_NON_US=1`` restores the old behaviour, for the day the plan changes."""
    return (os.environ.get(NON_US_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def skip_non_us(ticker: str) -> str:
    """The reason to skip, or ``""`` to call as usual."""
    if not is_non_us_symbol(ticker) or finnhub_non_us_enabled():
        return ""
    return non_us_reason(ticker)


class FinnhubAdapter(SentimentAdapter):
    name = "finnhub"

    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        # .strip(): stray whitespace pasted into an env var / notebook secret
        # must not be able to cause a silent HTTP 401.
        raw = api_key or os.environ.get("FINNHUB_API_KEY") or ""
        self._key = raw.strip() or None
        self._timeout = timeout
        if not self._key:
            raise SentimentDataUnavailable("FINNHUB_API_KEY is not set")

    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict) -> object:
        # FINNHUB-SKIP-1 — refuse before the socket, not after the 403. Guarding the one
        # transport seam covers every endpoint at once, including any added later.
        symbol = str(params.get("symbol") or "")
        skipped = skip_non_us(symbol)
        if skipped:
            raise SentimentDataUnavailable(skipped)
        params = {**params, "token": self._key}
        url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SentimentDataUnavailable(
                f"Finnhub {path} HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SentimentDataUnavailable(f"Finnhub {path}: {exc}") from exc

    # ------------------------------------------------------------------ #
    def get_company_news(
        self, ticker: str, *, start: date, end: date
    ) -> list[NewsItem]:
        raw = self._get("/company-news", {
            "symbol": ticker,
            "from": start.isoformat(),
            "to": end.isoformat(),
        })
        if not isinstance(raw, list):
            raise SentimentDataUnavailable("Finnhub /company-news: bad payload")
        items: list[NewsItem] = []
        for r in raw:
            try:
                ts = datetime.fromtimestamp(int(r["datetime"]), tz=timezone.utc)
                items.append(NewsItem(
                    published=ts.date(),
                    headline=str(r.get("headline", "")).strip(),
                    source=str(r.get("source", "")),
                ))
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed rows, keep the rest
        return items

    def get_recommendation_trends(
        self, ticker: str
    ) -> list[RecommendationTrend]:
        raw = self._get("/stock/recommendation", {"symbol": ticker})
        if not isinstance(raw, list):
            raise SentimentDataUnavailable(
                "Finnhub /stock/recommendation: bad payload"
            )
        trends: list[RecommendationTrend] = []
        for r in raw:
            try:
                trends.append(RecommendationTrend(
                    period=str(r.get("period", "")),
                    strong_buy=int(r.get("strongBuy", 0)),
                    buy=int(r.get("buy", 0)),
                    hold=int(r.get("hold", 0)),
                    sell=int(r.get("sell", 0)),
                    strong_sell=int(r.get("strongSell", 0)),
                ))
            except (TypeError, ValueError):
                continue
        return trends
