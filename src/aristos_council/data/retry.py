"""Adapter armor — a transient failure is NOT absent data (hardening ITEM 5).

A rate-limit 429 and a delisting look identical once every provider error is flattened
to ``DataUnavailable`` — so UNRATEABLE could misfire on a live ticker that just got
throttled. ``RetryAdapter`` wraps a market-data adapter, retries TRANSIENT failures
(timeout / 429 / 5xx / connection) with exponential backoff, and CLASSIFIES the outcome:

- transient AND still failing after the attempts  -> raise ``TransientFetchError``
  (the pipeline aborts that name with a 'fetch failed — rerun' status, never UNRATEABLE);
- not transient (a clean 404 / empty result)      -> re-raise unchanged (ABSENT ->
  UNRATEABLE as before), with NO wasted retries.

Compose it BENEATH the cache (``CachingAdapter(RetryAdapter(raw))``) so a cache hit skips
both the network and the retry.
"""

from __future__ import annotations

import time
from typing import Callable

from .adapter import MarketDataAdapter, TransientFetchError

# Substrings that mark a provider error as TRANSIENT (retryable). Matched
# case-insensitively against the exception text the adapters already produce.
_TRANSIENT_MARKERS = (
    "429", "rate limit", "ratelimit", "too many requests",
    "timeout", "timed out", "connection", "connectionerror", "temporarily",
    "500", "502", "503", "504", "5xx", "server error", "unavailable",
)


def is_transient(exc: BaseException) -> bool:
    """True if the exception looks transient (retryable) rather than absent-data."""
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


class RetryAdapter(MarketDataAdapter):
    def __init__(self, inner: MarketDataAdapter, *, attempts: int = 3,
                 base_delay: float = 0.5, sleep: Callable[[float], None] = time.sleep):
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base = base_delay
        self._sleep = sleep
        self.name = inner.name
        self.dividend_streak_method = inner.dividend_streak_method

    def provider_for(self, data_kind: str) -> str:
        return self._inner.provider_for(data_kind)

    def _retry(self, fn: Callable, what: str):
        last: BaseException | None = None
        for attempt in range(self._attempts):
            try:
                return fn()
            except TransientFetchError:
                raise                                 # already classified downstream
            except Exception as exc:                  # noqa: BLE001 — classify, then act
                if not is_transient(exc):
                    raise                             # ABSENT (clean 404 / empty) -> pass
                last = exc
                if attempt < self._attempts - 1:
                    self._sleep(self._base * (2 ** attempt))   # exponential backoff
        raise TransientFetchError(
            f"{what}: {self._attempts} attempts failed transiently ({last})")

    def get_fundamentals(self, ticker):
        return self._retry(lambda: self._inner.get_fundamentals(ticker),
                           f"get_fundamentals {ticker}")

    def get_price_history(self, ticker, *, start, end):
        return self._retry(
            lambda: self._inner.get_price_history(ticker, start=start, end=end),
            f"get_price_history {ticker}")

    def get_dividend_history(self, ticker, *, start, end):
        return self._retry(
            lambda: self._inner.get_dividend_history(ticker, start=start, end=end),
            f"get_dividend_history {ticker}")

    def get_street_consensus(self, ticker):
        return self._retry(lambda: self._inner.get_street_consensus(ticker),
                           f"get_street_consensus {ticker}")
