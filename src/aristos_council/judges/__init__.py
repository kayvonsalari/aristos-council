"""JUDGE-PANEL-1 - a blind, multi-model judge panel on the company page.

It records what independent models (one per family) would have said about a company, shown only
its raw facts, so that in twelve months we can judge whether LLM judgement adds anything. It has no
vote: the lenses stay the verdict of record and nothing here feeds a lens, a rank, a badge, the
agreement table or the shortlist. See docs/JUDGE_PANEL.md.

Kept apart from the council opinion on purpose: this package imports nothing from the council
graph, and the council opinion imports nothing from here.

    blind_pack  the facts pack, stripped of every lens opinion
    answer      the one JSON answer shape and its strict validation
    config      data/judges.yaml - which models
    clients     one HTTPS call per family
    panel       run the judges, stability, split, agreement with the record
    narrate     the narrator's short paragraph when the panel differs
    log         judge_log.csv / judge_summary.csv under data/local/judges/
    render      the rows and lines every surface shares
    report      ``python -m aristos_council.judges report``
"""
