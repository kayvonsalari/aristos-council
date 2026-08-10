"""NARR-INDEP-TEST harness — claims.py."""

from __future__ import annotations

from experiments.narr_indep_test.claims import (
    Claim, compare_claims, extract_claims, extract_claims_by_sentence,
    find_quantitative_mentions, flag_temporal_claims)

_TABLE = {"N": 5, "combined_position": 1, "ticker": "VUSA.AS", "score": 4.0,
         "factors": {"expense_ratio": 1.0, "fund_size": 1.0, "distribution_yield": 1.0,
                     "momentum_12m": 1.0}}


def test_extract_claims_finds_ordinal_absolute_and_rank_sum():
    text = ("VUSA.AS ranks 1st of 5 overall. Its expense ratio is 0.07%. The combined "
            "rank-sum of 4 confirms it.")
    claims = extract_claims(text, _TABLE)
    kinds = {(c.kind, c.subject) for c in claims}
    assert ("ordinal", "combined") in kinds
    assert ("absolute", "expense_ratio") in kinds
    assert ("rank_sum", "combined") in kinds


def test_multi_factor_clause_is_skipped_never_guessed():
    # naming BOTH expense_ratio and fund_size in one clause is ambiguous — no absolute
    # claim should be invented by mis-pairing the number with the wrong factor.
    text = "It has a 0.07% expense ratio and a USD 43.0bn fund size in one breath."
    claims = extract_claims(text, _TABLE)
    absolutes = [c for c in claims if c.kind == "absolute"]
    assert absolutes == []


def test_extract_claims_by_sentence_keeps_original_sentence_text():
    # _sentences() (narration_check's splitter, reused as-is) strips the terminal
    # period as the split delimiter — this pins that the ORIGINAL (not the
    # markdown/polarity-stripped) sentence text is what comes back.
    text = "VUSA.AS ranks 1st of 5 overall."
    pairs = extract_claims_by_sentence(text, _TABLE)
    assert len(pairs) == 1
    sentence, claims = pairs[0]
    assert sentence == "VUSA.AS ranks 1st of 5 overall"
    assert any(c.kind == "ordinal" for c in claims)


def test_compare_claims_matches_within_tolerance():
    a = [Claim("absolute", "fund_size", 43_000_000_000.0)]
    b = [Claim("absolute", "fund_size", 42_990_000_000.0)]     # 0.02% apart
    cmp = compare_claims(a, b)
    assert len(cmp["matched"]) == 1
    assert cmp["value_changed"] == [] and cmp["only_in_a"] == [] and cmp["only_in_b"] == []


def test_compare_claims_flags_a_real_value_change():
    a = [Claim("absolute", "expense_ratio", 0.07)]
    b = [Claim("absolute", "expense_ratio", 0.70)]              # 10x — not tolerance noise
    cmp = compare_claims(a, b)
    assert cmp["matched"] == []
    assert len(cmp["value_changed"]) == 1


def test_compare_claims_flags_a_claim_only_on_one_side():
    a = [Claim("ordinal", "combined", 1.0), Claim("absolute", "fund_size", 43e9)]
    b = [Claim("ordinal", "combined", 1.0)]
    cmp = compare_claims(a, b)
    assert len(cmp["matched"]) == 1
    assert cmp["only_in_a"] == [Claim("absolute", "fund_size", 43e9)]
    assert cmp["only_in_b"] == []


def test_flag_temporal_claims_catches_ungrounded_trend_language():
    text = "Fees have been rising steadily. The fee itself is 0.07%."
    flagged = flag_temporal_claims(text)
    assert len(flagged) == 1
    assert "rising" in flagged[0]


def test_flag_temporal_claims_empty_on_point_in_time_prose():
    text = "The expense ratio is 0.07%, the best in the cohort."
    assert flag_temporal_claims(text) == []


def test_find_quantitative_mentions_does_not_require_subject_binding():
    # unlike extract_claims, this must catch a rank claim even with NOTHING to bind to
    # (the B1 ablation case — an empty table would make extract_claims miss it).
    text = "This name ranks 3rd of 5 based on its typical profile."
    mentions = find_quantitative_mentions(text)
    kinds = {m["kind"] for m in mentions}
    assert "digit_ordinal" in kinds


def test_find_quantitative_mentions_empty_on_a_genuine_disclaimer():
    text = "No factor or rank data is available for this name in this evidence pack."
    assert find_quantitative_mentions(text) == []
