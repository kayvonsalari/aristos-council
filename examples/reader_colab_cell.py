# ----- step 4b - READER-1: one plain-English summary for ONE cohort (needs a key)
#
# Paste this cell into aristos_oil_cohorts_colab.py directly AFTER step 4. It is kept in
# the repo so it is version-controlled alongside the code it exercises; the Colab notebook
# itself lives in Drive.
#
# It makes exactly ONE model call, for ONE cohort, on the cheapest tier. Step 4 above stays
# free and untouched: this re-runs a single cohort with `with_reader=True` rather than
# turning the summary on for all of them, because the point is to read one and judge it.
#
# NO KEY -> it prints one line and skips. It never raises and never blocks the notebook.

import os

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("READER-1: no ANTHROPIC_API_KEY in the environment - skipping the summary.")
else:
    from aristos_council.pipeline import run_multi_strategy_pipeline
    from aristos_council.reader import reader_paragraphs

    # The cohort to summarise. Any id from COHORTS; the oil dividend list is the one the
    # summary was designed against.
    READER_COHORT = "oil_dividend_v1"
    _c = next((c for c in COHORTS if c["id"] == READER_COHORT), COHORTS[0])

    print(f"=== READER-1 on {_c['id']} ({len(_c['tickers'])} names) - one model call ===")
    _res = run_multi_strategy_pipeline(
        universe_id=_c["id"], universes_dir=UNIVERSES_DIR, strategy_ids=LENSES,
        strategies_dir=STRATEGIES_DIR, ranker_only=True, use_cache=True,
        with_valuation_band=True, freeze_dir=Path(ROOT) / "runs",
        # SHORTLIST-3: no primary lens. Every lens in LENSES is a vote of equal weight and
        # the shortlist is the agreement between them, so there is nothing to nominate.
        # What the list was built for, stated in the summary. Blank is fine - no claim.
        cohort_thesis=_c.get("thesis", ""),
        with_reader=True,
        progress=lambda m: print("   ", m))

    _reader = _res.reader
    print()
    if _reader is None:
        print("no summary was requested")
    elif not _reader.available:
        # A withheld summary is the honest outcome, not a failure: the note says why.
        print(_reader.note)
    else:
        for _lead, _text in reader_paragraphs(_reader.summary):
            print(f"**{_lead}** {_text}\n")

    # ACCEPTANCE ITEM 4 - what it cost. The reader runs on its own CostMeter, so the run
    # records the provider's real token counts and price rather than an estimate. Recorded
    # whether the summary was published or withheld: a withheld one was still paid for.
    _m = _res.meta.get("reader") or {}
    print("--- meta['reader'] ---")
    for _k in ("model", "temperature", "prompt_version", "written", "words", "check"):
        if _k in _m:
            print(f"  {_k}: {_m[_k]}")
    if "input_tokens" in _m:
        _usd = _m.get("usd")
        _price = f"${_usd:.4f}" if _usd is not None else "not priced (unknown model rate)"
        print(f"  tokens: {_m['input_tokens']} in / {_m['output_tokens']} out - {_price}")
    else:
        print("  tokens: not recorded (no metered call was made)")
