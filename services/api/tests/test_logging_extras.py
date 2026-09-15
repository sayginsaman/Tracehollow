"""Logging calls must not use reserved LogRecord attribute names as ``extra`` keys.

Such a call raises KeyError when the record is created, which only happens when the level is
enabled, so ordinary tests (WARNING level) do not notice it. Found on the running stack for an
INFO message in Phase 2.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

RESERVED = set(vars(logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None))) | {
    "message",
    "asctime",
}


def test_no_logging_extra_key_overwrites_a_log_record_attribute() -> None:
    offenders = []
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and key.value in RESERVED:
                        offenders.append(f"{path}:{node.lineno} uses {key.value!r}")
    assert offenders == []
