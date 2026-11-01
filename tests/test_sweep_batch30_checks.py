"""Sweep checks added with Batch 30: the symptoms of DIV-STREAK-1 and VALE-EPV-ABSTAIN-1."""
from aristos_council.sweep import checks as C


def test_a_short_streak_stated_flat_over_a_short_history_is_found():
    lines = ["| Defensive Income | excluded - 4 consecutive years of dividend increases (history on file starts 2021); "
             "the rule requires at least 10. |"]
    hits = C.streak_short_findings(lines, this_year=2026)
    assert len(hits) == 1 and "starts 2021" in hits[0][1]


def test_an_at_least_streak_or_a_long_history_is_not_a_finding():
    ok = ["at least 4 consecutive years of dividend increases (history on file starts 2021)",
          "4 consecutive years of dividend increases (history on file starts 1962)",
          "4 consecutive years of dividend increases"]
    assert C.streak_short_findings(ok, this_year=2026) == []


def test_raw_ten_digit_money_in_reader_text_is_found_and_short_money_is_not():
    bad = ["the provider's headline revenue (215,830,003,712) and its latest annual statement (38,403,000,000)"]
    assert len(C.raw_digit_findings(bad)) == 2
    assert C.raw_digit_findings(["headline revenue $215.8bn vs latest statement $38.4bn, 5.6x apart", "2026-10-31",
                                 "market cap 12,345,678"]) == []


def test_both_rules_run_inside_check_markdown():
    text = "headline revenue 215,830,003,712 is odd\n"
    rules = {f.rule for f in C.check_markdown(text, entry="X")}
    assert C.R_RAW_DIGITS in rules and C.R_RAW_DIGITS in C.ALL_RULES and C.R_STREAK_SHORT in C.ALL_RULES
