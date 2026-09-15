"""Schemas shared across routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

MAX_PAGE_SIZE = 100

LimitParam = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
OffsetParam = Annotated[int, Query(ge=0, le=1_000_000)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
