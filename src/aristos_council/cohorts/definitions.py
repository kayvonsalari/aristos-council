"""COHORT-1 — what a cohort IS, before anything is built.

A cohort is a RULE, not a list of names. The rule lives in
``data/local/cohorts/definitions.yaml``, the membership is derived from it mechanically,
and nothing in this package accepts a hand-picked ticker: ``anchors`` is the one place a
name may be written down, and an anchor that fails the rules is dropped like any other
name (it is a thing to LOOK AT in the report, never a thing to let in).

Nothing here imports a model, a runner or langchain. See ``test_cohorts_no_llm.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

# --------------------------------------------------------------------------- #
# the vocabulary
# --------------------------------------------------------------------------- #
# Exclusions every cohort carries unless it says otherwise. Financials and REITs are out
# by default because the lenses' arithmetic does not mean the same thing for them: a bank's
# balance sheet is its product, so leverage, ROIC and EV-based earnings yield read as
# alarming or as nonsense rather than as information. This is the same judgment the rank
# strategies already make one name at a time; a cohort makes it once, out loud.
DEFAULT_EXCLUDE: tuple[str, ...] = ("financials", "reits")

# EODHD's sector/industry strings that each exclusion keyword removes. Matched on the
# name's SECTOR first (broad) and then its INDUSTRY (narrow), because "Financial Services"
# is a sector while "REIT - Retail" is an industry.
_EXCLUDE_SECTORS: dict[str, tuple[str, ...]] = {
    "financials": ("Financial Services", "Financial"),
    "reits": ("Real Estate",),
}
_EXCLUDE_INDUSTRY_PREFIXES: dict[str, tuple[str, ...]] = {
    "financials": ("Banks", "Bank -", "Insurance", "Capital Markets", "Asset Management",
                   "Credit Services", "Financial Data", "Financial Conglomerates",
                   "Mortgage Finance", "Shell Companies"),
    "reits": ("REIT",),
}

# GICS sub-industry -> EODHD industry. The brief allows a definition to name either, and
# the two taxonomies are close but not the same: EODHD's ``General::GicSubIndustry`` is
# GICS, its ``General::Industry`` is the Yahoo-style string the index-constituent rows
# carry. The constituent rows are what the builder filters on (one call for 500 names
# instead of 500 calls), so a GICS name is mapped to its EODHD equivalent up front rather
# than silently matching nothing.
#
# Only the pairs this repo's ten definitions can reach are listed. An unknown GICS name is
# an ERROR at load, not a silent empty cohort — see ``normalise_industries``.
GICS_TO_EODHD: dict[str, tuple[str, ...]] = {
    "Integrated Oil & Gas": ("Oil & Gas Integrated",),
    "Oil & Gas Exploration & Production": ("Oil & Gas E&P",),
    "Oil & Gas Refining & Marketing": ("Oil & Gas Refining & Marketing",),
    "Electric Utilities": ("Utilities - Regulated Electric",),
    "Multi-Utilities": ("Utilities - Diversified",),
    "Pharmaceuticals": ("Drug Manufacturers - General",
                        "Drug Manufacturers - Specialty & Generic"),
    "Biotechnology": ("Biotechnology",),
    "Packaged Foods & Meats": ("Packaged Foods",),
    "Soft Drinks & Non-alcoholic Beverages": ("Beverages - Non-Alcoholic",),
    "Brewers": ("Beverages - Brewers",),
    "Distillers & Vintners": ("Beverages - Wineries & Distilleries",),
    "Industrial Machinery & Supplies & Components": ("Specialty Industrial Machinery",),
    "Agricultural & Farm Machinery": ("Farm & Heavy Construction Machinery",),
    "Semiconductors": ("Semiconductors",),
    "Semiconductor Materials & Equipment": ("Semiconductor Equipment & Materials",),
    "Application Software": ("Software - Application",),
    "Systems Software": ("Software - Infrastructure",),
    "Integrated Telecommunication Services": ("Telecom Services",),
    "Specialty Chemicals": ("Specialty Chemicals",),
    "Commodity Chemicals": ("Chemicals",),
    "Consumer Staples Merchandise Retail": ("Discount Stores",),
    "Food Retail": ("Grocery Stores",),
    "Food Distributors": ("Food Distribution",),
}

# Every EODHD industry string the ten shipped definitions use, so a typo in the YAML is
# caught at LOAD rather than as a cohort that mysteriously builds to nothing. Derived from
# a live probe of GSPC.INDX + STOXX.INDX constituents on 2026-09-18 (132 distinct values;
# these are the ones the definitions reference).
KNOWN_EODHD_INDUSTRIES: frozenset[str] = frozenset({
    "Beverages - Brewers", "Beverages - Non-Alcoholic",
    "Beverages - Wineries & Distilleries", "Biotechnology", "Chemicals",
    "Confectioners", "Discount Stores", "Drug Manufacturers - General",
    "Drug Manufacturers - Specialty & Generic", "Farm & Heavy Construction Machinery",
    "Food Distribution", "Grocery Stores", "Oil & Gas E&P",
    "Oil & Gas Equipment & Services", "Oil & Gas Integrated", "Oil & Gas Midstream",
    "Oil & Gas Refining & Marketing", "Packaged Foods", "Semiconductor Equipment & Materials",
    "Semiconductors", "Software", "Software - Application", "Software - Infrastructure",
    "Specialty Chemicals", "Specialty Industrial Machinery", "Specialty Retail",
    "Telecom Services", "Utilities - Diversified",
    "Utilities - Independent Power Producers", "Utilities - Regulated Electric",
    "Utilities - Regulated Gas", "Utilities - Regulated Water", "Utilities - Renewable",
})

# Exchange code -> the EODHD suffixes that count as "this exchange". EURONEXT and SIX are
# umbrella names in the brief; EODHD codes the individual books, so one entry maps to
# several. Main listings only — no OTC, no unsponsored ADR venues.
EXCHANGE_CODES: dict[str, tuple[str, ...]] = {
    "US": ("US",),
    "XETRA": ("XETRA", "F", "STU"),
    "LSE": ("LSE", "IL"),
    "EURONEXT": ("PA", "AS", "BR", "LS", "IR"),
    "SIX": ("SW",),
    # Present so a definition CAN reach the Nordics when a cohort comes back thin — the
    # "too thin" suggestion names them, and naming a thing the loader would reject would
    # be a cruel joke.
    "NORDIC": ("ST", "CO", "OL", "HE"),
    "MILAN": ("MI",),
    "MADRID": ("MC",),
    "VIENNA": ("VI",),
    "WARSAW": ("WAR",),
}

MIN_MEMBERS = 20
MAX_MEMBERS = 60


class DefinitionError(ValueError):
    """A definition file that cannot be trusted to build the same cohort twice."""


# --------------------------------------------------------------------------- #
# the definition
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CohortDefinition:
    """One rule. Frozen, because a definition that changed under a build would make the
    frozen membership unreproducible — the whole point of writing it down."""

    name: str
    industry: tuple[str, ...]
    exchanges: tuple[str, ...]
    min_market_cap: float
    min_history_years: float
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    anchors: tuple[str, ...] = ()
    # What the YAML said before ``normalise_industries`` mapped GICS names across, kept so
    # the snapshot beside a frozen cohort reads the way the author wrote it.
    industry_as_written: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        """Directory name: lowercase, spaces and punctuation to underscores.

        Stable across a rename of the display name only if the display name does not
        change — which is why a rename means a new cohort, not a new version of this one.
        """
        return re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")

    @property
    def exchange_codes(self) -> tuple[str, ...]:
        """The EODHD exchange suffixes this cohort accepts, flattened and de-duped."""
        out: list[str] = []
        for name in self.exchanges:
            for code in EXCHANGE_CODES[name]:
                if code not in out:
                    out.append(code)
        return tuple(out)


def normalise_industries(raw: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(eodhd_industries, as_written)`` — accept either taxonomy, store both.

    A GICS sub-industry name is mapped through ``GICS_TO_EODHD``; an EODHD industry is
    kept as-is. Anything in neither table raises, because the alternative is a cohort that
    builds to zero names and reports "too thin" for a reason that has nothing to do with
    the market.
    """
    names = [raw] if isinstance(raw, str) else list(raw or ())
    as_written = tuple(str(n).strip() for n in names if str(n).strip())
    if not as_written:
        raise DefinitionError("industry is required — a cohort with no industry is not a rule")

    resolved: list[str] = []
    for name in as_written:
        if name in KNOWN_EODHD_INDUSTRIES:
            mapped: tuple[str, ...] = (name,)
        elif name in GICS_TO_EODHD:
            mapped = GICS_TO_EODHD[name]
        else:
            raise DefinitionError(
                f"unknown industry {name!r} — not an EODHD industry and not a GICS "
                f"sub-industry this repo maps. Known EODHD values are in "
                f"cohorts.definitions.KNOWN_EODHD_INDUSTRIES; GICS names it maps are in "
                f"GICS_TO_EODHD.")
        for m in mapped:
            if m not in resolved:
                resolved.append(m)
    return tuple(resolved), as_written


def definition_from_mapping(raw: dict) -> CohortDefinition:
    """One YAML entry -> a definition, with every field validated here and nowhere else."""
    if not isinstance(raw, dict):
        raise DefinitionError(f"a cohort definition must be a mapping, got {type(raw).__name__}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise DefinitionError("every cohort needs a name")

    industry, as_written = normalise_industries(raw.get("industry"))

    exchanges = tuple(str(e).strip().upper() for e in (raw.get("exchanges") or ()) if str(e).strip())
    if not exchanges:
        raise DefinitionError(f"{name}: exchanges is required (main listings only)")
    unknown = [e for e in exchanges if e not in EXCHANGE_CODES]
    if unknown:
        raise DefinitionError(
            f"{name}: unknown exchange(s) {unknown} — known: {sorted(EXCHANGE_CODES)}")

    try:
        min_cap = float(raw.get("min_market_cap", 0) or 0)
        min_hist = float(raw.get("min_history_years", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise DefinitionError(f"{name}: min_market_cap / min_history_years must be numbers") from exc
    if min_cap <= 0:
        raise DefinitionError(f"{name}: min_market_cap must be positive")
    if min_hist <= 0:
        raise DefinitionError(f"{name}: min_history_years must be positive")

    # ``exclude:`` absent means the default; an EXPLICIT empty list means the author
    # deliberately turned the exclusions off, and that is allowed. ``None`` and a missing
    # key are the same thing and both mean "default on" (house rule 3 in miniature: an
    # absent value is not a false one).
    if "exclude" in raw and raw["exclude"] is not None:
        exclude = tuple(str(x).strip().lower() for x in raw["exclude"] if str(x).strip())
        unknown_ex = [x for x in exclude if x not in _EXCLUDE_SECTORS]
        if unknown_ex:
            raise DefinitionError(
                f"{name}: unknown exclude keyword(s) {unknown_ex} — known: "
                f"{sorted(_EXCLUDE_SECTORS)}")
    else:
        exclude = DEFAULT_EXCLUDE

    anchors = tuple(str(a).strip().upper() for a in (raw.get("anchors") or ()) if str(a).strip())
    if len(anchors) > 2:
        raise DefinitionError(
            f"{name}: at most 2 anchors ({len(anchors)} given). An anchor is a name to "
            f"look at in the report, not a way to hand-pick membership.")

    return CohortDefinition(
        name=name, industry=industry, exchanges=exchanges, min_market_cap=min_cap,
        min_history_years=min_hist, exclude=exclude, anchors=anchors,
        industry_as_written=as_written)


def load_definitions(path: str | Path) -> list[CohortDefinition]:
    """Every definition in the file, in file order. Duplicate names are refused."""
    import yaml

    path = Path(path)
    if not path.exists():
        raise DefinitionError(f"no definition file at {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("cohorts") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise DefinitionError(
            f"{path}: expected a top-level 'cohorts:' list, got {type(entries).__name__}")

    out: list[CohortDefinition] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        try:
            defn = definition_from_mapping(entry)
        except DefinitionError as exc:
            raise DefinitionError(f"{path} entry {i + 1}: {exc}") from None
        if defn.slug in seen:
            raise DefinitionError(f"{path}: two cohorts resolve to the slug {defn.slug!r}")
        seen.add(defn.slug)
        out.append(defn)
    return out


def find_definition(defs: list[CohortDefinition], name: str) -> CohortDefinition:
    """By display name or by slug, case-insensitively. Raises if there is no match."""
    want = (name or "").strip().lower()
    for d in defs:
        if d.name.lower() == want or d.slug == re.sub(r"[^a-z0-9]+", "_", want).strip("_"):
            return d
    raise DefinitionError(
        f"no cohort named {name!r} — defined: {', '.join(d.name for d in defs) or '(none)'}")


def excluded_by(defn: CohortDefinition, sector: str, industry: str) -> str:
    """The exclusion keyword this name trips, or ``""``.

    Sector first, then industry prefix — a REIT's sector is Real Estate, but a specialist
    financial's sector can be anything while its industry is unmistakable.
    """
    sector = (sector or "").strip()
    industry = (industry or "").strip()
    for keyword in defn.exclude:
        if sector and sector in _EXCLUDE_SECTORS.get(keyword, ()):
            return keyword
        for prefix in _EXCLUDE_INDUSTRY_PREFIXES.get(keyword, ()):
            if industry.startswith(prefix):
                return keyword
    return ""


def with_exchanges(defn: CohortDefinition, exchanges: tuple[str, ...]) -> CohortDefinition:
    """A copy widened to more exchanges — used to COST a 'too thin' suggestion, never to
    change what was built. The builder writes the definition it was given."""
    return replace(defn, exchanges=tuple(dict.fromkeys(defn.exchanges + exchanges)))
