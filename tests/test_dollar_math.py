"""DOLLAR-MATH-1 (Batch 17 item 1). Live: Hugo Boss / Novocure "worth $3.0bn, below the $5bn
rule" lost both "$" to LaTeX maths and a "**" stayed raw. Pure string tests plus a real
Streamlit run (AppTest) that checks what reaches the element tree."""
from __future__ import annotations

import pytest

from aristos_council.ui_text import escape_dollars


def test_two_amounts_keep_both_dollars_and_no_stray_markers():
    out = escape_dollars("This company is worth $3.0bn, below the $5bn rule. **Outside** "
                         "the cohort, $2bn-$5bn")
    assert out.count("\$") == 4 and "$" not in out.replace("\$", "")
    assert out.count("**") == 2


def test_escape_is_idempotent_and_leaves_code_alone():
    once = escape_dollars("cost $1 and $2")
    assert escape_dollars(once) == once
    assert escape_dollars("see `$HOME` and $5") == "see `$HOME` and \$5"
    assert escape_dollars(None) is None and escape_dollars("no money") == "no money"


def test_every_markdown_call_is_wrapped_by_install():
    pytest.importorskip("streamlit")        # CI installs no UI extra
    from streamlit.testing.v1 import AppTest

    script = '''
import streamlit as st
from aristos_council.ui_text import install
install(st)
st.markdown("worth $3.0bn, below the $5bn rule")
st.info("1.9bn $x and $y")
st.warning("**Outside the tested range (under $5bn)**")
st.caption("cohort, $2bn-$5bn")
st.write("write $1 and $2")
col, = st.columns(1)
col.markdown("col $1 $2")
'''
    at = AppTest.from_string(script).run()
    assert not at.exception
    shown = ([m.value for m in at.markdown] + [i.value for i in at.info]
             + [w.value for w in at.warning] + [c.value for c in at.caption])
    assert len(shown) >= 6
    for text in shown:
        assert text.count("$") == text.count("\$"), text   # no bare "$" reaches the page
