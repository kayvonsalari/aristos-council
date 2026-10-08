"""B25-4 - the Earnings Power Value explanation was backwards. MSFT 2026-10-08: the council said a more negative
reading (-65.4%) "signals a larger discount, which the lens treats as bullish". It is the reverse: -65.4% means the
price is far ABOVE what today's operating profit alone is worth; MSFT ranked 2nd only because its peers were dearer."""
from types import SimpleNamespace

from aristos_council.agents.nodes import _cross_lens_block
from aristos_council.ai_text_check import plain_reason
from aristos_council.company_report import LensVote, _epv_reading
from aristos_council.factors import direction_in_words
from aristos_council.narration_check import check_epv_direction


def test_every_factor_has_a_direction_in_plain_words():
    assert direction_in_words("epv_margin_of_safety").startswith("higher is better")
    assert "ABOVE" in direction_in_words("epv_margin_of_safety") and "never call it a discount" in \
        direction_in_words("epv_margin_of_safety")
    assert direction_in_words("momentum_12m") == "higher is better"
    assert direction_in_words("no_such_factor") == ""


def test_the_direction_words_never_contain_the_word_rank():
    assert "rank" not in direction_in_words("epv_margin_of_safety").lower()


def test_the_narrator_block_tells_it_to_use_the_stated_direction():
    rows = [{"lens": "Earnings Power Value", "cell": "BUY - 2nd of 13", "votes": True, "factors": [
        "Earnings power value vs. enterprise value: -65.4%, rank 2 of 13 (" + direction_in_words("epv_margin_of_safety")
        + ")"]}]
    block = _cross_lens_block(SimpleNamespace(cross_lens_verdicts=rows, cross_lens_reasons=[]))
    assert "describe a reading by THAT direction" in block and "above the no-growth value" in block
    assert "higher is better" in block


def _vote(buy_above):
    return LensVote(strategy_id="epv_v1", label="Earnings Power Value", status="ranked", verdict="buy",
                    position=2, cohort_size=13, buy_above_value=buy_above)


def test_an_epv_buy_on_a_negative_reading_says_it_is_the_best_of_an_expensive_group():
    v = _vote(True)
    assert v.expensive_note == "best of an expensive group: still priced above its no-growth value"
    assert v.expensive_note in v.result()


def test_an_epv_buy_on_a_positive_reading_adds_nothing():
    v = _vote(False)
    assert v.expensive_note == "" and "expensive" not in v.result()


def test_the_check_flags_a_negative_reading_called_a_discount():
    text = ("A more negative reading on this factor signals that the company's capitalised earnings power trades at "
            "a larger discount to enterprise value, which the lens treats as a bullish signal.")
    flags = check_epv_direction(text)
    assert len(flags) == 1 and "AI text check" in flags[0]
    assert "not a discount" in plain_reason(flags[0])
    assert check_epv_direction("The Earnings Power Value lens ranked MSFT 2nd on a reading of -65.4%, so it looks cheap.")


def test_the_check_leaves_correct_sentences_alone():
    assert check_epv_direction("MSFT's EPV reading of -65.4% means the price is above its no-growth value, "
                               "not a discount.") == []
    assert check_epv_direction("A positive EPV reading means the price is at a discount to earnings power.") == []
    assert check_epv_direction("The sector trades at a discount to its history.") == []


def test_the_reading_is_read_from_the_ranked_row():
    row = SimpleNamespace(ticker="MSFT", excluded=False, factor_values={"epv_margin_of_safety": -0.654})
    result = SimpleNamespace(ranked=[row])
    assert _epv_reading(result, "msft") == -0.654
    assert _epv_reading(SimpleNamespace(ranked=[]), "MSFT") is None
