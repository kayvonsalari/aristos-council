"""Tier 1 eval suite — DETERMINISTIC process guarantees (no LLM, no network).

These freeze the override-matrix behaviour of the trust-critical subsystems — the
disposition gate, the INSUFFICIENT_EVIDENCE short-circuit, and the human-veto gate
— as fast assertions over HAND-CONSTRUCTED screen results. They assert PROCESS
("a confirmed gating fail caps to SELL"), never market outcomes ("a stock went
up"). They run in CI under ``.[dev]`` with no API key and no network.

Tier 2 (DEFERRED) — anything that needs live councils / LLM output: Critic
quality, trend-CAGR honesty, verdict correctness. NOT in this package.
"""
