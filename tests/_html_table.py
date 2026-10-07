"""Read the rows back out of an ``ar-table`` HTML table the app renders (UI-POLISH-1), so a test can
assert the same cells it used to read from ``st.table``."""
from __future__ import annotations

import re
from html import unescape


def _text(fragment: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\$", "$").strip()


def table_rows(markdown_values, *, must_have: str = "Reason") -> list[dict]:
    """``[{header: cell text}]`` for the first ar-table among ``markdown_values`` carrying ``must_have``."""
    for value in markdown_values:
        value = str(value)
        if 'class="ar-table"' not in value or f">{must_have}<" not in value:
            continue
        heads = [_text(h) for h in re.findall(r"<th>(.*?)</th>", value)]
        rows = []
        for tr in re.findall(r"<tbody>(.*?)</tbody>", value, re.S)[0].split("</tr>"):
            cells = [re.sub(r"\s+", " ", _text(c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if cells:
                rows.append(dict(zip(heads, cells)))
        return rows
    return []
