"""DATA-HYGIENE-1 — fund_size base-currency normalisation (the pure layer).

Why this exists
---------------
EODHD reports an ETF's total assets in the FUND'S BASE CURRENCY — not in the currency of
the listing we quote it under. Verified live: IQQH's ``4,172,812,800`` matches Citywire's
"4.2bn USD" (the fund's base currency is USD) while the German factsheets quote EUR. So a
cross-fund ``fund_size`` ranking was comparing USD against EUR against GBP amounts and
silently distorting the ordering of any cohort whose funds report in different currencies.

This module is the conversion itself: a fund size + its SOURCE currency + a DATED FX rate
becomes one EUR amount that carries its own receipt. The disciplines are the codebase's:

- **Nothing is invented.** No source currency -> NO conversion (the caller flags the value
  as unverified or abstains — it never guesses a currency). No FX rate -> ABSTAIN; a
  mixed-currency number is worse than a missing one.
- **It shows its work.** A converted value renders
  ``4.17bn USD @ 0.86 EUR/USD, 2026-07-29`` inside the factor's provenance receipt, the
  same way the accounts->price FX conversion does (``factors.CurrencyConversion``).
- **Pure + offline.** The FX FETCH lives at the edge (``factors.gather_factor_inputs``,
  through the same cached/frozen adapter price path), so this module has no network and
  replays byte-identically in a frozen run.

Abstention semantics are UNCHANGED: a fund_size that is None before this layer is None
after it, and the ETF lens abstains on it exactly as it already did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Every fund_size is normalised to, stored as, and displayed in this currency. EUR is the
# reporting currency of the ETF universes this repo ranks (UCITS funds, XETRA/AS/L
# listings) and of the factsheets a human verifies the static rows against.
FUND_SIZE_CCY = "EUR"

# The note surfaced when a SERVED fund_size has no known source currency: the value is
# passed through UNCONVERTED (its pre-fix behaviour) but is never presented as EUR. Used
# for the committed static rows written before the currency column existed — flagged, not
# silently reinterpreted.
UNVERIFIED_CCY_NOTE = "fund size currency unverified — not normalised to EUR"

# The note surfaced when the source currency IS known but the dated FX rate could not be
# fetched. The value is then WITHHELD (the factor abstains) — never mixed into a
# EUR-denominated ranking.
FX_UNAVAILABLE_NOTE = "fund size FX rate unavailable — abstained"


def normalize_currency_code(raw: object) -> Optional[str]:
    """A currency code as an upper-case ISO-ish token, or None when absent/unusable.

    Trims and upper-cases (``" usd "`` -> ``"USD"``). Anything that is not a 3-letter
    alphabetic code is None — an absence, not a guess: we would rather abstain than
    convert with a code we cannot interpret (``"$"``, ``"NA"``, ``"EURO"``)."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def compact_amount(value: float) -> str:
    """A fund size in the compact form the receipt quotes (``4172812800`` -> ``4.17bn``).

    Display only — the stored/ranked number is always the full float. Scales at tn/bn/m
    with three significant digits; smaller values render plainly."""
    magnitude = abs(value)
    for scale, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m")):
        if magnitude >= scale:
            return f"{value / scale:.3g}{suffix}"
    return f"{value:g}"


@dataclass(frozen=True)
class FundSizeConversion:
    """One fund_size converted from its base currency to EUR at a DATED rate.

    ``rate`` is units of ``to_ccy`` per 1 ``from_ccy`` (USD->EUR ~ 0.86), so
    ``value == source_value * rate``. ``as_of`` is the date the rate was read
    ('YYYY-MM-DD'), which the receipt quotes — an undated FX rate is unauditable."""

    source_value: float
    from_ccy: str
    rate: float
    as_of: str
    to_ccy: str = FUND_SIZE_CCY

    @property
    def value(self) -> float:
        """The normalised amount, in ``to_ccy``."""
        return self.source_value * self.rate

    @property
    def tag(self) -> str:
        """The provenance receipt clause, e.g.
        ``4.17bn USD @ 0.86 EUR/USD, 2026-07-29`` — carries the SOURCE currency, the rate
        and the rate's date, so the conversion can be re-checked by hand."""
        return (f"{compact_amount(self.source_value)} {self.from_ccy} @ "
                f"{self.rate:.4g} {self.to_ccy}/{self.from_ccy}, {self.as_of}")


def needs_conversion(from_ccy: object) -> bool:
    """Is a KNOWN source currency something other than EUR? False for an unknown currency
    (nothing to convert — the caller flags it) and for EUR itself (already normalised)."""
    code = normalize_currency_code(from_ccy)
    return code is not None and code != FUND_SIZE_CCY


def convert_fund_size(value: Optional[float], from_ccy: object,
                      rate: Optional[float], as_of: str
                      ) -> Optional[FundSizeConversion]:
    """The conversion, or None when it cannot be made honestly.

    None (no conversion) when the value is missing, the source currency is unknown/
    uninterpretable, or the rate is missing/non-positive. The caller decides what a None
    means for that path — flag the unconverted value (unknown currency) or abstain
    (known currency, unavailable rate) — because those are different failures."""
    code = normalize_currency_code(from_ccy)
    if value is None or code is None:
        return None
    if rate is None or rate <= 0 or rate != rate:      # missing / nonsense / NaN
        return None
    return FundSizeConversion(source_value=float(value), from_ccy=code,
                              rate=float(rate), as_of=as_of)
