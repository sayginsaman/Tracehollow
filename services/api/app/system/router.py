from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from kombu.exceptions import OperationalError as KombuOperationalError
from pydantic import BaseModel, ConfigDict
from redis import RedisError
from sqlalchemy import select, update

from app import __version__
from app.db.base import utcnow
from app.deps import DbDep, PrincipalDep
from app.health import checks
from app.health.router import run_readiness_checks
from app.system.models import WorkerCheck, WorkerCheckStatus
from app.tasks.celery_app import DEFAULT_QUEUE, WORKER_CHECK_TASK, TracehollowCelery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class DependencyCheck(BaseModel):
    status: checks.CheckStatus
    detail: str | None


class SystemStatus(BaseModel):
    api_version: str
    environment: str
    ready: bool
    checks: dict[str, DependencyCheck]
    checked_at: datetime


class WorkerNode(BaseModel):
    name: str


class WorkerStatus(BaseModel):
    status: Literal["online", "offline", "broker_unavailable"]
    workers: list[WorkerNode]
    checked_at: datetime
    note: str


class WorkerCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    requested_at: datetime
    dispatched_at: datetime | None
    completed_at: datetime | None
    worker_hostname: str | None
    error_code: str | None


def _celery(request: Request) -> TracehollowCelery:
    app: TracehollowCelery = request.app.state.celery
    return app


@router.get("/status")
def system_status(request: Request, _principal: PrincipalDep) -> SystemStatus:
    results = run_readiness_checks(request)
    return SystemStatus(
        api_version=__version__,
        environment=request.app.state.settings.env,
        ready=all(result.ok for result in results.values()),
        checks={
            name: DependencyCheck(status=result.status, detail=result.detail)
            for name, result in results.items()
        },
        checked_at=utcnow(),
    )


@router.get("/worker")
def worker_status(request: Request, _principal: PrincipalDep) -> WorkerStatus:
    """Ask running workers to answer a broadcast ping through the broker."""
    timeout = request.app.state.settings.worker_ping_timeout_seconds
    try:
        replies = _celery(request).control.ping(timeout=timeout)
    except (KombuOperationalError, RedisError, OSError) as exc:
        logger.warning("worker_ping_broker_unavailable", extra={"error_type": type(exc).__name__})
        return WorkerStatus(
            status="broker_unavailable",
            workers=[],
            checked_at=utcnow(),
            note="The broker could not be reached, so worker health is unknown.",
        )
    names = sorted(name for reply in replies or [] for name in reply)
    return WorkerStatus(
        status="online" if names else "offline",
        workers=[WorkerNode(name=name) for name in names],
        checked_at=utcnow(),
        note=(
            f"{len(names)} worker(s) answered a broker ping within {timeout:g} s."
            if names
            else f"No worker answered a broker ping within {timeout:g} s."
        ),
    )


@router.post("/worker-checks", status_code=status.HTTP_202_ACCEPTED)
def create_worker_check(
    request: Request, response: Response, db: DbDep, principal: PrincipalDep
) -> WorkerCheckOut:
    """Queue a minimal task; a worker marks it completed in PostgreSQL when it runs."""
    check = WorkerCheck(status=WorkerCheckStatus.QUEUED, requested_by_user_id=principal.user.id)
    db.add(check)
    db.commit()
    try:
        _celery(request).send_task(
            WORKER_CHECK_TASK,
            args=[str(check.id)],
            queue=DEFAULT_QUEUE,
            retry=True,
            retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.5},
        )
    except (KombuOperationalError, RedisError, OSError) as exc:
        logger.warning("worker_check_dispatch_failed", extra={"error_type": type(exc).__name__})
        db.execute(
            update(WorkerCheck)
            .where(WorkerCheck.id == check.id, WorkerCheck.status == WorkerCheckStatus.QUEUED)
            .values(status=WorkerCheckStatus.DISPATCH_FAILED, error_code="broker_unavailable")
        )
        db.commit()
        db.refresh(check)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return WorkerCheckOut.model_validate(check)
    db.execute(update(WorkerCheck).where(WorkerCheck.id == check.id).values(dispatched_at=utcnow()))
    db.commit()
    db.refresh(check)
    return WorkerCheckOut.model_validate(check)


@router.get("/worker-checks")
def list_worker_checks(
    db: DbDep,
    _principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[WorkerCheckOut]:
    rows = db.scalars(select(WorkerCheck).order_by(WorkerCheck.requested_at.desc()).limit(limit))
    return [WorkerCheckOut.model_validate(row) for row in rows]


@router.get("/worker-checks/{check_id}")
def get_worker_check(check_id: uuid.UUID, db: DbDep, _principal: PrincipalDep) -> WorkerCheckOut:
    check = db.get(WorkerCheck, check_id)
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="worker_check_not_found")
    return WorkerCheckOut.model_validate(check)
