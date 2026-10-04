"""DOLLAR-MATH-1 — keep "$" amounts from being swallowed as LaTeX maths.

Streamlit renders markdown with maths on: two "$" in one string turn everything between them
into an equation ("worth $3.0bn, below the $5bn rule" showed as "worth 3.0bn, below the 5bn
rule" in a maths font, and a "**" inside the span stayed raw). The documented cure is "backslash-dollar".

``escape_dollars`` is the one implementation (idempotent, leaves code spans alone) and
``install`` wraps every Streamlit text call that renders markdown, so no call site — present
or future — can forget. Pure string work; importing this needs no Streamlit.
"""
from __future__ import annotations

import re

_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
_BARE_DOLLAR = re.compile(r"(?<!\\)\$")


def escape_dollars(text):
    r"""``$`` -> ``\$`` outside code spans; an already-escaped ``\$`` is left as is."""
    if not isinstance(text, str) or "$" not in text:
        return text
    parts = _CODE.split(text)
    # re.split with one capture group: odd indexes are the code spans themselves
    return "".join(p if i % 2 else _BARE_DOLLAR.sub(r"\$", p) for i, p in enumerate(parts))


_TEXT_CALLS = ("markdown", "info", "warning", "error", "success", "caption", "write")
_INSTALLED = "_aristos_dollar_safe"


def _clean(args, kwargs):
    # leave HTML pages alone: the "$" there is not parsed as maths, and an escape would print
    # as a stray backslash. Escaping a non-body str (an icon) is harmless; non-str is untouched.
    if kwargs.get("unsafe_allow_html"):
        return args, kwargs
    args = tuple(escape_dollars(a) for a in args)
    if "body" in kwargs:
        kwargs = {**kwargs, "body": escape_dollars(kwargs["body"])}
    return args, kwargs


def _wrap_function(fn):
    if getattr(fn, _INSTALLED, False):
        return fn

    def safe(*args, **kwargs):
        args, kwargs = _clean(args, kwargs)
        return fn(*args, **kwargs)

    safe.__wrapped__ = fn
    setattr(safe, _INSTALLED, True)
    return safe


def _wrap_method(original):
    def safe(self, *args, **kwargs):
        args, kwargs = _clean(args, kwargs)
        return original(self, *args, **kwargs)

    safe.__wrapped__ = original
    setattr(safe, _INSTALLED, True)
    return safe


def install(st) -> None:
    """Wrap the markdown-rendering calls on the ``streamlit`` module AND on the delta
    generator (``col.markdown``, ``with st.container(): ...``). Idempotent."""
    from streamlit.delta_generator import DeltaGenerator

    for name in _TEXT_CALLS:
        method = getattr(DeltaGenerator, name, None)
        if method is not None and not getattr(method, _INSTALLED, False):
            setattr(DeltaGenerator, name, _wrap_method(method))
        module_fn = getattr(st, name, None)
        if module_fn is not None:
            setattr(st, name, _wrap_function(module_fn))
