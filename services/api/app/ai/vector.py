"""Minimal pgvector column type.

Values are exchanged with PostgreSQL in pgvector's text form (``[1,2,3]``), so no additional
database driver plugin is needed. Dimensions are not fixed on the column: each row records its
own dimension count, and a check constraint keeps the two consistent.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def get_col_spec(self, **_: Any) -> str:
        return "vector"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Sequence[float] | None) -> str | None:
            return None if value is None else to_literal(value)

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(item) for item in value.strip("[]").split(",") if item]

        return process


def to_literal(values: Sequence[float]) -> str:
    """Render a vector literal, rejecting non-finite values instead of storing them."""
    parts = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("embedding contains a non-finite value")
        parts.append(repr(number))
    return "[" + ",".join(parts) + "]"
