"""SWEEP-1 - one command that runs the awkward-company test round end to end, unattended.

    python -m aristos_council.sweep run [--set awkward] [--judges/--no-judges] [--council]
                                        [--workers N] [--out DIR]

See ``docs/SWEEP.md``. ``sets`` parses the set file, ``producers`` runs the real pipeline,
``runner`` orchestrates and writes the manifest, ``checks`` holds the report checks and
``summary`` writes ``summary.md``.
"""
