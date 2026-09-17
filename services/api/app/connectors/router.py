"""Source capabilities, credential status and health (the "Sources" screen)."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cases.models import CaseMember
from app.config import Settings
from app.connectors.base import CapabilityStatus, Connector, ConnectorDescriptor
from app.connectors.registry import all_connectors, get_connector
from app.connectors.schemas import (
    CapabilityOut,
    ConnectorDescriptorOut,
    ConnectorHealthOut,
    CredentialIn,
    CredentialStatusOut,
    ParameterSpecOut,
)
from app.db.base import utcnow
from app.deps import DbDep, Principal, PrincipalDep, SettingsDep
from app.integrations import service as integrations
from app.integrations.service import CredentialStatus
from app.queries.models import ConnectorRun

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

HEALTH_WINDOW = timedelta(days=30)


def _health(db: Session, principal: Principal, connector_id: str) -> ConnectorHealthOut:
    accessible = select(CaseMember.case_id).where(CaseMember.user_id == principal.user.id)
    base = select(ConnectorRun).where(
        ConnectorRun.connector_id == connector_id,
        ConnectorRun.case_id.in_(accessible),
        ConnectorRun.finished_at.is_not(None),
    )
    last = db.scalar(base.order_by(ConnectorRun.finished_at.desc()).limit(1))
    recent = Counter(
        {
            str(outcome or "none"): int(count)
            for outcome, count in db.execute(
                select(ConnectorRun.outcome, func.count())
                .where(
                    ConnectorRun.connector_id == connector_id,
                    ConnectorRun.case_id.in_(accessible),
                    ConnectorRun.finished_at >= utcnow() - HEALTH_WINDOW,
                )
                .group_by(ConnectorRun.outcome)
            )
        }
    )
    return ConnectorHealthOut(
        last_run_at=last.finished_at if last else None,
        last_outcome=last.outcome if last else None,
        last_error_code=last.last_error_code if last else None,
        last_quota=last.quota_usage if last else None,
        recent_outcomes=dict(recent),
    )


def _capabilities(
    settings: Settings, descriptor: ConnectorDescriptor, credentials: list[CredentialStatus]
) -> list[CapabilityOut]:
    usable = {item.spec.name for item in credentials if item.usable}
    result = []
    for spec in descriptor.capabilities:
        blocked: str | None = None
        if spec.status != CapabilityStatus.IMPLEMENTED:
            blocked = spec.reason or "No adapter implements this capability."
        else:
            missing = [name for name in spec.credential_names if name not in usable]
            if missing:
                blocked = (
                    "Blocked until credentials are configured: " + ", ".join(missing) + ". "
                    "Collection and live verification cannot run without them."
                )
            elif spec.name == "public_profile_page" and not settings.instagram_public_web_enabled:
                blocked = (
                    "Disabled by configuration (TRACEHOLLOW_INSTAGRAM_PUBLIC_WEB_ENABLED=false)."
                )
        result.append(
            CapabilityOut(
                name=spec.name,
                label=spec.label,
                status=spec.status,
                access_method=spec.access_method,
                provider=spec.provider,
                collection_mode=spec.collection_mode,
                account_types=list(spec.account_types),
                content_types=list(spec.content_types),
                returned_fields=list(spec.returned_fields),
                unavailable_fields=list(spec.unavailable_fields),
                stable_identifiers=list(spec.stable_identifiers),
                pagination=spec.pagination,
                session_requirements=spec.session_requirements,
                restrictions=spec.restrictions,
                cost_quota=spec.cost_quota,
                verification_status=spec.verification_status,
                last_live_verification=spec.last_live_verification,
                credential_names=list(spec.credential_names),
                reason=spec.reason,
                references=list(spec.references),
                available=blocked is None,
                blocked_reason=blocked,
            )
        )
    return result


def _describe(
    db: Session, settings: Settings, principal: Principal, connector: Connector
) -> ConnectorDescriptorOut:
    d = connector.descriptor
    credential_statuses = integrations.statuses(db, settings, d.connector_id)
    return ConnectorDescriptorOut(
        connector_id=d.connector_id,
        version=d.version,
        display_name=d.display_name,
        synthetic=d.synthetic,
        description=d.description,
        supported_input_types=list(d.supported_input_types),
        collection_mode=d.collection_mode,
        credential_requirements=d.credential_requirements,
        coverage=d.coverage,
        max_pages=d.max_pages,
        max_items_per_page=d.max_items_per_page,
        timeout_seconds=d.timeout_seconds,
        retry_max_attempts=d.retry_policy.max_attempts,
        retryable_outcomes=[str(o) for o in d.retry_policy.retryable_outcomes],
        output_schema=d.output_schema,
        cost_model=d.cost_model,
        quota_notes=d.quota_notes,
        cache_policy=d.cache_policy,
        max_concurrent_runs=d.max_concurrent_runs,
        min_request_interval_seconds=d.min_request_interval_seconds,
        provider_terms=d.provider_terms,
        documentation=d.documentation,
        last_live_verification=d.last_live_verification,
        verification_status=d.verification_status,
        parameters=[
            ParameterSpecOut(
                name=p.name,
                kind=p.kind,
                label=p.label,
                description=p.description,
                default=p.default,
                choices=dict(p.choices) if p.choices is not None else None,
                minimum=p.minimum,
                maximum=p.maximum,
            )
            for p in d.parameters
        ],
        credentials=[
            CredentialStatusOut(
                name=item.spec.name,
                label=item.spec.label,
                description=item.spec.description,
                required=item.spec.required,
                configured=item.configured,
                usable=item.usable,
                updated_at=item.updated_at,
                last_used_at=item.last_used_at,
                last_result=item.last_result,
            )
            for item in credential_statuses
        ],
        health=_health(db, principal, d.connector_id),
        capabilities=_capabilities(settings, d, credential_statuses),
    )


@router.get("")
def list_connectors(
    db: DbDep, settings: SettingsDep, principal: PrincipalDep
) -> list[ConnectorDescriptorOut]:
    return [_describe(db, settings, principal, c) for c in all_connectors()]


def _require_admin(principal: Principal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="administrator_required")


@router.post("/{connector_id}/credentials/{name}")
def set_credential(
    connector_id: str,
    name: str,
    body: CredentialIn,
    db: DbDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> ConnectorDescriptorOut:
    """Store or replace a credential. The value is write-only: it is never returned."""
    _require_admin(principal)
    integrations.set_credential(db, settings, connector_id, name, body.value, principal.user)
    db.commit()
    connector = get_connector(connector_id)
    assert connector is not None
    return _describe(db, settings, principal, connector)


@router.delete("/{connector_id}/credentials/{name}")
def delete_credential(
    connector_id: str, name: str, db: DbDep, settings: SettingsDep, principal: PrincipalDep
) -> ConnectorDescriptorOut:
    _require_admin(principal)
    integrations.delete_credential(db, connector_id, name)
    db.commit()
    connector = get_connector(connector_id)
    assert connector is not None
    return _describe(db, settings, principal, connector)
