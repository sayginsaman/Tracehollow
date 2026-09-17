"""Case budgets: configured limits and current-period usage."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.audit.service import record
from app.budgets import service as budgets
from app.budgets.models import BudgetMetric, BudgetPeriod, BudgetScope, CaseBudget
from app.cases.access import ReadableCase, WritableCase
from app.db.base import utcnow
from app.deps import ActorDep, DbDep, PrincipalDep
from app.monitoring.router import usage_out
from app.monitoring.schemas import UsageOut

router = APIRouter(prefix="/api/v1/cases/{case_id}/budgets", tags=["budgets"])


class CaseBudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["requests", "provider_units"]
    period: Literal["day", "week", "month"]
    limit_units: Annotated[int, Field(ge=0, le=10_000_000)]


class CaseBudgetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The complete set of case budgets; budgets left out are removed.
    budgets: Annotated[list[CaseBudgetIn], Field(max_length=6)]


class CaseBudgetsOut(BaseModel):
    budgets: list[CaseBudgetIn]
    usage: list[UsageOut]
    measurement: str


MEASUREMENT = (
    "Requests are counted when Tracehollow sends them, including retries, pagination and requests "
    "that failed. Engines that run in their own process (username and domain discovery) are "
    "counted as estimates. Provider units are documented per-request quota costs, not measured by "
    "the provider, and never money. Periods are UTC calendar days, ISO weeks and months."
)


def _out(db: DbDep, case_id: object) -> CaseBudgetsOut:
    rows = list(db.scalars(select(CaseBudget).where(CaseBudget.case_id == case_id)))
    requirements = budgets.case_requirements(db, rows[0].case_id) if rows else []
    return CaseBudgetsOut(
        budgets=[
            CaseBudgetIn(metric=row.metric, period=row.period, limit_units=row.limit_units)
            for row in rows
        ],
        usage=[usage_out(budgets.current_usage(db, requirement)) for requirement in requirements],
        measurement=MEASUREMENT,
    )


@router.get("")
def get_budgets(case: ReadableCase, db: DbDep) -> CaseBudgetsOut:
    return _out(db, case.id)


@router.put("")
def set_budgets(
    case: WritableCase, db: DbDep, principal: PrincipalDep, actor: ActorDep, body: CaseBudgetsIn
) -> CaseBudgetsOut:
    keys = [(item.metric, item.period) for item in body.budgets]
    if len(keys) != len(set(keys)):
        from fastapi import HTTPException, status

        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "duplicate_budget", "message": "Set each metric and period once."},
        )
    existing = {
        (row.metric, row.period): row
        for row in db.scalars(
            select(CaseBudget).where(CaseBudget.case_id == case.id).with_for_update()
        )
    }
    for key, row in existing.items():
        if key not in keys:
            db.delete(row)
    now = utcnow()
    for item in body.budgets:
        current = existing.get((item.metric, item.period))
        if current is None:
            db.add(
                CaseBudget(
                    case_id=case.id,
                    metric=item.metric,
                    period=item.period,
                    limit_units=item.limit_units,
                    updated_by_user_id=principal.user.id,
                )
            )
        else:
            current.limit_units = item.limit_units
            current.updated_by_user_id = principal.user.id
            current.updated_at = now
        budgets.apply_limit_change(
            db,
            budgets.Requirement(
                scope_type=BudgetScope.CASE,
                scope_id=case.id,
                metric=BudgetMetric(item.metric),
                period=BudgetPeriod(item.period),
                limit_units=item.limit_units,
            ),
        )
    removed = [key for key in existing if key not in keys]
    record(
        db,
        actor,
        "budget.updated",
        case_id=case.id,
        target_type="case",
        target_id=case.id,
        details={
            "budgets": [item.model_dump() for item in body.budgets],
            "removed": [f"{m}/{p}" for m, p in removed],
        },
    )
    db.commit()
    return _out(db, case.id)
