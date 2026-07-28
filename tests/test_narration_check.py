"""Narrator rank-semantics post-check (ITEM 4).

The narrator inverts/misstates rank ordinals while its numbers are right. The
deterministic post-check verifies ordinal superlative claims against the authoritative
rank table and APPENDS a machine annotation on a contradiction (never rewrites prose).
The four synthetic cases below reproduce verbatim error fixtures from saved exports;
correct ordinal statements must pass untouched.
"""

from __future__ import annotations

from aristos_council.narration_check import check_narration

# conservative run: DUK best (combined 9, position 1); SO second (combined 12, position 2),
# low_volatility rank 2. growth run: MRK roic rank 21 of 23.
_CONS_SO = {"N": 10, "combined_position": 2,
            "factors": {"low_volatility": 2, "net_payout_yield": 4, "momentum_12m": 6}}
_CONS_DUK = {"N": 10, "combined_position": 1,
             "factors": {"low_volatility": 1, "net_payout_yield": 3, "momentum_12m": 5}}
_GROWTH_MRK = {"N": 23, "combined_position": 20, "factors": {"roic": 21}}


def _flagged(narrative, table) -> bool:
    flags = check_narration(narrative, table)
    return len(flags) == 1 and "contradicts rank table" in flags[0]


# --------------------------------------------------------------------------- #
# The four verbatim error fixtures — each must be flagged
# --------------------------------------------------------------------------- #
def test_fixture_1_rank_2_is_not_second_worst():
    assert _flagged(
        "A rank of 2 out of 10 means SO is the second-worst name in the cohort.",
        _CONS_SO)


def test_fixture_2_combined_12_is_not_the_best():
    assert _flagged(
        "SO carries a combined rank-sum of 12 — the best (lowest) in the cohort.",
        _CONS_SO)


def test_fixture_3_so_low_vol_is_not_best_in_cohort():
    assert _flagged("SO shows the best-in-cohort low volatility of the group.", _CONS_SO)


def test_fixture_4_roic_rank_21_of_23_is_not_second_worst():
    assert _flagged("MRK's ROIC rank 21 out of 23 makes it second-worst on quality.",
                    _GROWTH_MRK)


# --------------------------------------------------------------------------- #
# Correct ordinal statements — must pass untouched
# --------------------------------------------------------------------------- #
def test_correct_second_best_passes():
    assert check_narration(
        "SO ranks 2 out of 10 — second-best in the cohort.", _CONS_SO) == []


def test_correct_best_combined_passes():
    assert check_narration(
        "DUK carries a combined rank-sum of 9 — the best (lowest) in the cohort.",
        _CONS_DUK) == []


def test_correct_best_in_cohort_low_vol_passes():
    assert check_narration(
        "DUK shows the best-in-cohort low volatility of the group.", _CONS_DUK) == []


def test_second_least_volatile_is_not_parsed_as_a_superlative():
    # the correct body phrasing that contradicted fixture 3's summary — not an ordinal
    # token this check recognizes, so it is left alone (no false flag).
    assert check_narration("SO is the second-least-volatile name in the cohort.",
                           _CONS_SO) == []


def test_third_from_the_bottom_is_not_flagged():
    # rank 21 of 23 IS third-worst — 'bottom' is deliberately not an ordinal token.
    assert check_narration("MRK's ROIC rank 21 out of 23 sits third from the bottom.",
                           _GROWTH_MRK) == []


def test_no_ordinal_no_flag():
    assert check_narration("SO earnings look mid-pack; roic rank 5 out of 10.",
                           _CONS_SO) == []


def test_empty_or_degenerate_table_is_safe():
    assert check_narration("", _CONS_SO) == []
    assert check_narration("second-worst name, rank 2 out of 10", {"N": 0}) == []


# --------------------------------------------------------------------------- #
# NARR-CHK-1 — parser fixes: the three 2026-07-11 false positives must pass
# silently; the 2026-07-10 ASML true positive must still flag.
# --------------------------------------------------------------------------- #
# garp_v2 run: NVDA is 1st/2nd/4th/6th on the four factors (rank-sum 13, best overall).
_GARP_NVDA = {"N": 7, "combined_position": 1,
              "factors": {"revenue_growth": 1, "roic": 2, "momentum_12m": 4,
                          "earnings_yield": 6}}
# LLY sits 2nd of 7 overall — "near-best" is an approximation, not a rank-1 claim.
_GARP_LLY = {"N": 7, "combined_position": 2,
             "factors": {"revenue_growth": 3, "roic": 2, "momentum_12m": 2,
                         "earnings_yield": 4}}
# 2026-07-10 growth run: NVO's rank-sum 8 beat ASML's 9, so ASML is 2nd overall, NOT best.
_GROWTH_ASML = {"N": 10, "combined_position": 2,
                "factors": {"revenue_growth": 3, "roic": 2, "momentum_12m": 4,
                            "earnings_yield": 1}}


def test_nvda_multi_factor_sentence_passes_silently():
    # defect (b): ordinals bound to the factor each NAMES — all four are correct.
    assert check_narration(
        "1st on revenue_growth, 2nd on ROIC, 4th on momentum_12m, 6th on earnings_yield, "
        "sum 13 lowest.", _GARP_NVDA) == []


def test_lly_near_best_lines_pass_silently():
    # a hedged superlative ("near-best") is an approximation, not a rank-1 claim.
    assert check_narration("Rank 2/7 (near-best).\nRank 2/7 (near-best).", _GARP_LLY) == []


def test_asml_best_combined_still_flags():
    # the true positive stays caught: ASML claims the best combined rank-sum, but NVO's 8
    # beats its 9 -> ASML is 2nd.
    assert _flagged("ASML has the best combined rank-sum in the cohort.", _GROWTH_ASML)


def test_correct_ordinals_in_arbitrary_factor_order_pass():
    # factors named out of table-column order — each must validate against its own name.
    assert check_narration(
        "6th on earnings_yield, 1st on revenue_growth, 4th on momentum_12m, 2nd on ROIC.",
        _GARP_NVDA) == []


def test_genuine_numeric_contradiction_still_annotates():
    # a digit ordinal that is wrong for the factor it names is still flagged.
    assert _flagged("NVDA sits 1st on ROIC in the cohort.", _GARP_NVDA)


def test_decimal_is_atomic_claim_not_truncated():
    # defect (a): "31.4" must not split the sentence; the flagged claim carries it whole.
    flags = check_narration(
        "With a revenue CAGR of 31.4%, ASML has the best combined rank-sum.",
        _GROWTH_ASML)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]
    assert "31.4" in flags[0]                    # the decimal survived intact, not "31"


def test_decimal_sentence_does_not_spuriously_split_or_flag():
    # a correct sentence with a decimal stays one sentence and passes.
    assert check_narration(
        "NVDA posted a revenue CAGR of 31.4% and ranks 1st on revenue_growth.",
        _GARP_NVDA) == []


# --------------------------------------------------------------------------- #
# NARR-CHK-2 — three new false-positive classes on the 2026-07-11 financials run
# must pass silently; the ASML true positive + all NARR-CHK-1 fixtures stay as-is.
# --------------------------------------------------------------------------- #
# financials run: GS is 4th overall (combined 21), 3rd on momentum of 16.
_FIN_GS = {"N": 16, "combined_position": 4, "ticker": "GS",
           "factors": {"price_to_book": 11, "return_on_equity": 7, "momentum_12m": 3}}


def test_theoretical_bound_arithmetic_aside_passes():
    # class 1: "worst possible = 48" is cohort arithmetic (a theoretical bound), not a
    # claim that GS is worst.
    assert check_narration(
        "GS carries a combined rank-sum of 20 (lower is better; worst possible = 48).",
        _FIN_GS) == []


def test_generic_hypothetical_superlative_not_bound_to_the_name_passes():
    # class 2: "the best-ranked name" is a generic subject, not a claim that GS is best.
    assert check_narration(
        "Even the best-ranked name in the cohort is not insulated from sector-level "
        "drawdowns.", _FIN_GS) == []


def test_compound_relative_ordinal_third_best_passes():
    # class 3: "third-best" is rank 3 (true here), not the bare "best" (rank 1).
    assert check_narration(
        "Momentum (rank 3/16 — top quartile): GS's aggregate earns the third-best "
        "momentum rank.", _FIN_GS) == []


def test_narr_chk2_does_not_lose_the_asml_true_positive():
    # the ASML class of catch is preserved — a real "best combined rank-sum" on a
    # non-rank-1 name still flags.
    assert _flagged("ASML has the best combined rank-sum in the cohort.", _GROWTH_ASML)
    # and a genuine financials contradiction still annotates.
    assert _flagged("GS holds the best combined rank-sum in the cohort.", _FIN_GS)


def test_loose_cohort_claim_that_names_the_ticker_still_flags():
    # the legitimate catch survives: a claim that NAMES the narrated name and calls it
    # "the worst in the cohort" (GS is 4th of 16, not worst) still annotates — this is the
    # class the pipeline post-check test exercises.
    assert _flagged("GS is the worst name in the cohort.", _FIN_GS)


# --------------------------------------------------------------------------- #
# NARR-CHK-4 — two live etf_core_v1 (2026-07-21) false positives on correct rank
# prose must pass silently; a genuine ordinal inversion must still flag.
# --------------------------------------------------------------------------- #
# etf_core_v1 cohort of 5: this fund is 1st on fund_size, 2nd on expense_ratio, 5th (last)
# on momentum_12m — combined 2nd overall.
_ETF_CORE = {"N": 5, "combined_position": 2, "ticker": "VWCE",
             "factors": {"fund_size": 1, "expense_ratio": 2, "momentum_12m": 5}}


def test_multi_factor_word_superlatives_bind_per_clause_and_pass():
    # false positive 1: "best" (fund_size, rank 1), "second-best" (expense_ratio, rank 2),
    # "last" (momentum, rank 5) each bind to the factor their OWN clause names — all correct,
    # so the sentence passes untouched. The old whole-sentence check paired "best" with
    # momentum_12m (rank 5) and false-flagged.
    assert check_narration(
        "This means fund_size is the best in the cohort, expense_ratio is second-best, "
        "and momentum_12m is last in the cohort.", _ETF_CORE) == []


def test_negated_superlative_is_not_a_rank_claim_and_passes():
    # false positive 2: "(Competitive, Not Best)" DISCLAIMS rank 1 — true of a rank-2 name —
    # so the "Rank 2/5" citation must not be read as contradicting a (non-existent) rank-1
    # claim. Bold/markdown wrapping does not change this.
    assert check_narration(
        "**Cost Factor — Rank 2/5 (Competitive, Not Best)**", _ETF_CORE) == []


def test_true_word_superlative_inversion_still_flags():
    # a genuinely wrong ordinal claim stays caught: this fund is rank 5/5 on momentum
    # (last), yet the write-up calls it the strongest — a real inversion.
    assert _flagged(
        "VWCE's momentum rank 5/5 is described as the strongest in the cohort.", _ETF_CORE)


def test_wrong_word_superlative_bound_to_its_factor_still_flags():
    # per-clause binding does not weaken the catch: a clause that calls the rank-5 momentum
    # factor "the best" (no citation) is flagged against that factor's actual rank.
    assert _flagged("momentum_12m is the best in the cohort.", _ETF_CORE)


# --------------------------------------------------------------------------- #
# NARR-CHK-4 (cont.) — the two residual gaps behind the same 2026-07-21
# etf_core_v1 run: markdown emphasis defeating the hedge/negation skip, and the
# SXR8 "momentum 5/5 is exceptional" inversion being uncatchable.
# --------------------------------------------------------------------------- #
# Same 5-name cohort, the SXR8 sleeve: momentum_12m is 5/5 (the rank the bullish specialist
# called "exceptional"). The other ranks are synthetic — only the momentum 5/5 is on record.
_ETF_SXR8 = {"N": 5, "combined_position": 3, "ticker": "SXR8",
             "factors": {"fund_size": 2, "expense_ratio": 3, "momentum_12m": 5}}


def test_markdown_emphasis_inside_a_hedge_still_passes():
    # gap 1: "near-*best*" is the SAME hedge as the NARR-CHK-1 "near-best" fixture — the
    # emphasis marker sat between the hedge and the token and defeated the adjacency
    # `_SKIP_BEFORE` needs, so the rank-2 citation was read as contradicting a rank-1 claim
    # the prose never made.
    assert check_narration("Rank 2/5 (near-*best*).", _ETF_CORE) == []
    assert check_narration("Rank 2/5 (near-`best`).", _ETF_CORE) == []
    assert check_narration("Rank 2/5 (**near-best**).", _ETF_CORE) == []


def test_markdown_emphasis_inside_a_negation_still_passes():
    # gap 1, negation half: "**not** best" / "not **the** best" disclaim rank 1 exactly as
    # "Not Best" does; an intervening emphasis marker (or article) must not turn a disclaimer
    # into a claim.
    assert check_narration("Cost — Rank 2/5: **not** best on fees.", _ETF_CORE) == []
    assert check_narration("expense_ratio is not **the** best in the cohort.",
                           _ETF_CORE) == []


def test_backticked_factor_key_superlative_is_still_bound_and_flagged():
    # stripping markdown must not LOSE a catch: the same wrong claim about the rank-5 factor
    # is flagged whether or not the narrator code-quotes the key.
    assert _flagged("`momentum_12m` is the best in the cohort.", _ETF_CORE)


def test_bold_wrapped_true_inversion_still_flags_and_quotes_the_prose_verbatim():
    # annotate-don't-rewrite: parsing sees the de-marked text, the annotation quotes the
    # ORIGINAL sentence, emphasis markers included.
    flags = check_narration(
        "**SXR8's momentum rank 5/5 is the strongest in the cohort**", _ETF_SXR8)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]
    assert "**SXR8's momentum rank 5/5" in flags[0]


def test_sxr8_bare_cohort_citation_inversion_is_catchable():
    # gap 2: the verbatim SXR8 phrasing. "rank" is elided and "exceptional" is not a
    # best/worst synonym, so this slipped through entirely; momentum 5/5 is LAST, so calling
    # it exceptional is the inversion class the issue requires stay catchable.
    assert _flagged("SXR8's momentum 5/5 is exceptional.", _ETF_SXR8)
    assert _flagged("Momentum 5/5 — outstanding versus the cohort.", _ETF_SXR8)


def test_value_superlative_without_a_rank_citation_is_not_a_rank_claim():
    # the FP guard that makes gap 2 safe: "exceptional" is routinely predicated of a VALUE.
    # With no rank citation to bind it to, it is not a checkable ordinal claim — even when
    # the clause names a factor whose rank is last.
    assert check_narration(
        "SXR8 has delivered exceptional momentum over the trailing year.", _ETF_SXR8) == []
    assert check_narration("SXR8's expense ratio is outstanding at 7bps.", _ETF_SXR8) == []


def test_negated_value_superlative_passes():
    assert check_narration("SXR8's momentum 5/5 is not exceptional.", _ETF_SXR8) == []


def test_correct_cited_value_superlative_passes():
    # rank 1 genuinely IS exceptional — the citation agrees with the token, no flag.
    assert check_narration("VWCE's fund_size 1/5 is exceptional.", _ETF_CORE) == []


def test_bare_fraction_is_only_a_citation_at_cohort_size():
    # the bare-citation gate: a `d/d` whose denominator is not the cohort size N is a
    # fraction/date/rating, never a rank, so it binds nothing.
    assert check_narration("SXR8's momentum 5/7 is exceptional.", _ETF_SXR8) == []
    assert check_narration(
        "The KIID dated 2026/07/21 calls momentum exceptional.", _ETF_SXR8) == []


def test_bare_fraction_without_a_resolvable_subject_binds_nothing():
    # no factor / combined subject in the clause -> no bare citation, so nothing to check.
    assert check_narration("The wrapper scored 4/5 on liquidity and reads as exceptional.",
                           _ETF_SXR8) == []


# --------------------------------------------------------------------------- #
# NARR-CHK-5 — the 2026-07-28 ETF Index Tracker run: two polarity/binding false
# positives on TRUE prose must pass, and the rank-sum VALUE mismatch must flag
# whether the prose number is an integer or a decimal.
# --------------------------------------------------------------------------- #
# VUSA: momentum_12m 5/5 and the combined score 9.5 are ON RECORD from the run; the other two
# factor ranks are synthetic, chosen so they sum to that authoritative 9.5 (an averaged tie
# gives the half-point).
_ETF_VUSA = {"N": 5, "combined_position": 4, "ticker": "VUSA", "score": 9.5,
             "factors": {"fund_size": 1, "expense_ratio": 3.5, "momentum_12m": 5}}
# EUNL: expense_ratio 5/5 (worst), momentum_12m 3/5 (middling), tied #4 of 5 — all on record.
_ETF_EUNL = {"N": 5, "combined_position": 4, "ticker": "EUNL", "score": 10,
             "factors": {"fund_size": 2, "expense_ratio": 5, "momentum_12m": 3}}
# SXR8's combined score in the same run was 6.5; the factor ranks are synthetic and sum to it.
_ETF_SXR8_SCORED = {"N": 5, "combined_position": 2, "ticker": "SXR8", "score": 6.5,
                    "factors": {"fund_size": 2, "expense_ratio": 1.5, "momentum_12m": 3}}


def test_polarity_aware_superlative_on_a_worst_rank_passes():
    # false positive 1 (verbatim): rank 5/5 IS the strongest NEGATIVE signal — "strongest"
    # modifies "negative signal", not the fund's momentum standing, so the claim agrees with
    # the table and must pass. The mirror image of the true inversion below.
    assert check_narration(
        "12-Month Momentum (rank 5/5): This is the ranker's strongest negative signal and "
        "the central driver of the HOLD verdict.", _ETF_VUSA) == []


def test_polarity_aware_superlative_variants_pass():
    # the same reading for the other negative nouns the narrator reaches for — each is a
    # rank-5-of-5 claim in disguise and each matches the table.
    assert check_narration("Momentum (rank 5/5) is the strongest drag on the ranking.",
                           _ETF_VUSA) == []
    assert check_narration("Momentum rank 5/5 is the fund's strongest weakness.",
                           _ETF_VUSA) == []
    assert check_narration("Cost (rank 5/5) is EUNL's strongest downside.", _ETF_EUNL) == []


def test_bad_direction_superlative_on_a_negative_noun_is_ambiguous_and_dropped():
    # "its worst headwind" idiomatically means the most SEVERE headwind (rank 5), not the
    # mildest (rank 1) — the direction cannot be read off the words, so no claim is asserted
    # either way. Both the true and the false reading pass, deliberately.
    assert check_narration("Cost rank 5/5 is EUNL's worst headwind.", _ETF_EUNL) == []
    assert check_narration("Momentum rank 3/5 is EUNL's worst drag.", _ETF_EUNL) == []


def test_polarity_inversion_does_not_lose_the_true_superlative_inversion():
    # the discriminator: with no negative noun to modify, "the strongest" on a rank-5 momentum
    # is still the NARR-CHK-4 inversion — this fixture must keep failing the check.
    assert _flagged(
        "VWCE's momentum rank 5/5 is described as the strongest in the cohort.", _ETF_CORE)
    assert _flagged("VUSA's momentum rank 5/5 is the strongest in the cohort.", _ETF_VUSA)


def test_polarity_word_does_not_excuse_a_wrong_rank():
    # a mirrored superlative is still CHECKED, just against the mirror position: momentum is
    # 3/5 here, so calling it the strongest negative (= rank 5) contradicts the table.
    assert _flagged("Momentum rank 3/5 is the strongest negative signal.", _ETF_EUNL)


def test_multi_factor_clause_with_an_unbound_superlative_passes():
    # false positive 2 (verbatim): every clause matches the table — expense_ratio 5/5 (worst),
    # momentum 3/5 (middling), tied #4 of 5 (near-bottom). "worst-in-cohort" modifies the COST
    # rank, which sits on the other side of "and" from the only factor phrase the table
    # vocabulary recognises, so the pairing is ambiguous and the check must stand down.
    assert check_narration(
        "EUNL's worst-in-cohort cost rank and middling momentum rank produce a near-bottom "
        "combined ranking.", _ETF_EUNL) == []


def test_superlative_still_binds_to_the_factor_in_its_own_phrase():
    # the binding rule does not cost the catch: with no conjunction between them, a superlative
    # and the factor it names still pair — momentum is 3/5, not the best.
    assert _flagged("EUNL's momentum rank is the best in the cohort.", _ETF_EUNL)


def test_prose_rank_sum_integer_vs_half_point_table_value_flags():
    # the MISSED true positive: SXR8's authoritative score is 6.5, so prose asserting 6 is a
    # numeric hallucination — an integer never "rounds to" the table value.
    assert _flagged("SXR8 carries a combined rank-sum 6 across a 5-name cohort.",
                    _ETF_SXR8_SCORED)
    assert _flagged("Its combined rank-sum of 6 places it mid-cohort.", _ETF_SXR8_SCORED)


def test_prose_rank_sum_10_vs_9_point_5_still_flags():
    # the catch from the live run that must stay caught, now checked against the score itself.
    assert _flagged("VUSA carries a combined rank-sum of 10 across a 5-name cohort.",
                    _ETF_VUSA)


def test_correct_prose_rank_sum_passes():
    # the authoritative value, decimal or whole, is never flagged.
    assert check_narration("VUSA carries a combined rank-sum of 9.5 in this cohort.",
                           _ETF_VUSA) == []
    assert check_narration("EUNL's combined rank-sum of 10 is near the bottom.",
                           _ETF_EUNL) == []
    assert check_narration("SXR8's overall score 6.5 leads the tracker sleeve.",
                           _ETF_SXR8_SCORED) == []


def test_rank_sum_value_check_is_off_without_an_authoritative_score():
    # no score in the table -> nothing to compare against, so no value claim is invented.
    assert check_narration("SXR8 carries a combined rank-sum 6 across a 5-name cohort.",
                           _ETF_SXR8) == []


def test_theoretical_rank_sum_bounds_are_not_score_claims():
    # cohort arithmetic keeps its NARR-CHK-2 pass: the bounds are not this name's score.
    assert check_narration(
        "The best possible rank-sum is 3 and the worst possible rank-sum is 25.",
        _ETF_SXR8_SCORED) == []
    assert check_narration(
        "EUNL carries a combined rank-sum of 10 (lower is better; worst possible = 15).",
        _ETF_EUNL) == []


def test_a_peers_rank_sum_is_not_this_names_score():
    # a comparative aside cites ANOTHER name's rank-sum — not a claim about this one.
    assert check_narration("VWCE's combined rank-sum of 5 beats this sleeve.",
                           _ETF_SXR8_SCORED) == []


def test_out_of_bounds_number_beside_the_metric_word_is_not_a_score_claim():
    # 40 cannot be a rank-sum in a 3-factor, 5-name cohort (bounds 3..15), so the number is
    # some other quantity and binds nothing — the check never invents a mismatch.
    assert check_narration(
        "Its total score of 40 on the provider's own 100-point scale is a different metric.",
        _ETF_SXR8_SCORED) == []
