"""Worker and dispatcher processes import only part of the application. These tests start a fresh
interpreter so that missing model registrations (which in-process tests cannot see) fail."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]

PROBE = """
from sqlalchemy.orm import configure_mappers
import {module}
from app.config import get_settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory

create_session_factory(create_db_engine(get_settings()))
configure_mappers()
evidence = Base.metadata.tables["evidence_objects"]
for fk in evidence.foreign_keys:
    fk.column  # raises NoReferencedTableError if a referenced table is not registered
print(sorted(Base.metadata.tables))
"""


@pytest.mark.parametrize("module", ["app.tasks.worker", "app.dispatch.relay", "app.cli"])
def test_entrypoint_registers_every_table(module: str) -> None:
    env = {
        **os.environ,
        "TRACEHOLLOW_DATABASE_PASSWORD": "x" * 20,
        "TRACEHOLLOW_REDIS_PASSWORD": "y" * 20,
        "TRACEHOLLOW_SECRET_KEY": "z" * 40,
        "PYTHONPATH": str(API_ROOT),
    }
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", PROBE.format(module=module)],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "'users'" in result.stdout
    assert "'query_runs'" in result.stdout
