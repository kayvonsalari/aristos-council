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
