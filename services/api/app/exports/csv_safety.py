"""Spreadsheet formula neutralization for CSV exports (OWASP "CSV Injection").

Any cell whose text starts with a character that spreadsheet applications treat as the start of
a formula is prefixed with a single quote, which forces it to be displayed as text.
"""

from __future__ import annotations

from typing import Any

ASCII_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
# Full-width variants that some spreadsheet applications also evaluate.
FULLWIDTH_PREFIXES = tuple(chr(code) for code in (0xFF1D, 0xFF0B, 0xFF0D, 0xFF20))
FORMULA_PREFIXES = ASCII_PREFIXES + FULLWIDTH_PREFIXES


def neutralize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if text.lstrip(" ").startswith(FORMULA_PREFIXES):
        return "'" + text
    return text
