"""PILOT-STRIP-1 - the one setting that strips the app to its pilot shape.

Pilot mode is ON by default. It HIDES, and deletes nothing: the asset-type switch (stocks is always
used), the Display section, the Scoreboard tab, the cohort / list input, and the council-opinion and
judge-panel tick boxes; the plain-English summary is always written and has no tick box. Every hidden
control, its code and its tests are still there, and come back by setting ``ARISTOS_PILOT_MODE=0`` (in the
environment or in ``.env``) before the app starts.

Read at call time, never cached, so a test (or a restart with a changed ``.env``) sees the current value.
"""
from __future__ import annotations

import os

ENV = "ARISTOS_PILOT_MODE"
_OFF = {"0", "false", "off", "no"}


def pilot_mode(environ=None) -> bool:
    """True unless ``ARISTOS_PILOT_MODE`` is set to 0 / false / off / no (any case). Unset, empty or anything
    else means on: the default has to be the stripped app, not the full one."""
    env = os.environ if environ is None else environ
    return str(env.get(ENV, "") or "").strip().lower() not in _OFF
