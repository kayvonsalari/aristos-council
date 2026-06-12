"""Persistence layer — IO at the edge, so the graph stays pure.

The council graph never reads or writes disk; the verdict log is loaded before
`invoke` and appended after it (see examples/run_council.py). This keeps every
graph node deterministic and replayable.
"""
