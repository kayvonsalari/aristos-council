"""EXCHANGE-NAMES-1 (Batch 17 item 8): readable exchange names, one mapping table."""
from __future__ import annotations

from aristos_council import market_index
from aristos_council.exchange_names import EXCHANGE_NAMES, exchange_name


def test_every_code_the_index_can_store_has_a_readable_name():
    for code in market_index.DEFAULT_EXCHANGES:
        assert exchange_name(code) != "" and code in EXCHANGE_NAMES, code
    for venue in market_index.DEFAULT_VENUES["US"]:
        assert venue in EXCHANGE_NAMES, venue


def test_the_examples_from_the_hand_test():
    assert exchange_name("CO") == "Copenhagen"
    assert exchange_name("SW") == "SIX Swiss"
    assert exchange_name("PA") == "Euronext Paris"
    assert exchange_name("XETRA") == "Xetra"
    assert exchange_name("AU") == "ASX"
    assert exchange_name("TO") == "Toronto"


def test_unknown_and_empty_codes_are_shown_as_they_are_never_blanked():
    assert exchange_name("ZZ") == "ZZ"
    assert exchange_name("") == "" and exchange_name(None) == ""
    assert exchange_name("co") == "Copenhagen"          # case-insensitive fallback
