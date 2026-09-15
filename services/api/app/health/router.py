from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.health import checks

router = APIRouter(prefix="/api/health", tags=["health"])


class Liveness(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, checks.CheckStatus]


def run_readiness_checks(request: Request) -> dict[str, checks.CheckResult]:
    state = request.app.state
    results = checks.check_database(state.engine, state.expected_migration_heads)
    results["redis"] = checks.check_redis(state.redis)
    results["storage"] = checks.check_storage(state.settings.evidence_storage_path)
    return results


@router.get("/live")
def live() -> Liveness:
    """Process liveness only. Does not contact dependencies."""
    return Liveness(status="ok")


@router.get("/ready", responses={503: {"model": Readiness}})
def ready(request: Request, response: Response) -> Readiness:
    """Readiness of the API's required dependencies. Worker health is reported separately."""
    results = run_readiness_checks(request)
    all_ok = all(result.ok for result in results.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status="ready" if all_ok else "not_ready",
        checks={name: result.status for name, result in results.items()},
    )
