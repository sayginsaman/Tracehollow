"""Query execution engine.

Delivery is at-least-once. Correctness comes from PostgreSQL, not from the broker:

* A worker must *claim* a run (queued, or running with an expired lease) with a conditional
  update that issues a fresh lease token. A duplicate message cannot claim a run that another
  worker holds, and a finished run cannot be claimed at all.
* Work is split into bounded pages. Each page is persisted in one transaction together with
  its evidence file, observations, entities, relationships and progress counters. A unique
  ``(connector_run_id, page_index)`` constraint and observation idempotency keys make a
  re-executed page a no-op, so redelivery or lease takeover never duplicates logical results.
* Every page commit renews the lease only if the worker still holds the token.
* Cancellation is read from the database before each page and during retry waits.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.connectors.base import (
    Connector,
    ConnectorError,
    ConnectorPage,
    FetchRequest,
    ObservedAccount,
)
from app.connectors.fixture import SYNTHETIC_LABEL
from app.connectors.registry import get_connector
from app.db.base import utcnow
from app.db.session import session_scope
from app.dispatch import service as dispatch
from app.dispatch.models import AggregateType
from app.entities import normalize
from app.entities.models import (
    Entity,
    EntityEvidence,
    EntityIdentifier,
    EntityType,
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
    Stance,
)
from app.evidence.models import AcquisitionMethod, EvidenceKind, EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun, RunStatus

logger = logging.getLogger(__name__)

MAX_RETRY_WAIT_SECONDS = 30.0
_UNFINISHED = (RunStatus.QUEUED, RunStatus.RUNNING)


class LeaseLostError(Exception):
    """Another worker took over the run (or it finished); stop without writing."""


class CaseNotWritableError(Exception):
    pass


@dataclass
class ExecutionContext:
    session_factory: sessionmaker[Session]
    storage: EvidenceStorage
    settings: Settings
    worker_name: str
    sleep: Callable[[float], None] = time.sleep
    # Test hook: called after each committed page with (connector_run_id, page_index).
    after_page: Callable[[uuid.UUID, int], None] | None = field(default=None)


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # final run status, "skipped" or "lease_lost"


# -- lease handling ----------------------------------------------------------------------------


def claim_run(ctx: ExecutionContext, run_id: uuid.UUID) -> uuid.UUID | None:
    token = uuid.uuid4()
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        claimed = db.execute(
            update(QueryRun)
            .where(
                QueryRun.id == run_id,
                or_(
                    QueryRun.status == RunStatus.QUEUED,
                    and_(
                        QueryRun.status == RunStatus.RUNNING,
                        or_(QueryRun.lease_expires_at.is_(None), QueryRun.lease_expires_at < now),
                    ),
                ),
            )
            .values(
                status=RunStatus.RUNNING,
                started_at=func.coalesce(QueryRun.started_at, now),
                claim_count=QueryRun.claim_count + 1,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=ctx.settings.run_lease_seconds),
                error_code=None,
            )
            .returning(QueryRun.id)
        ).first()
    return token if claimed else None


def _renew(db: Session, ctx: ExecutionContext, run_id: uuid.UUID, token: uuid.UUID) -> None:
    renewed = db.execute(
        update(QueryRun)
        .where(
            QueryRun.id == run_id,
            QueryRun.lease_token == token,
            QueryRun.status == RunStatus.RUNNING,
        )
        .values(lease_expires_at=utcnow() + timedelta(seconds=ctx.settings.run_lease_seconds))
        .returning(QueryRun.id)
    ).first()
    if renewed is None:
        raise LeaseLostError


def _cancel_requested(db: Session, run_id: uuid.UUID) -> bool:
    return db.scalar(select(QueryRun.cancel_requested_at).where(QueryRun.id == run_id)) is not None


# -- entry point -------------------------------------------------------------------------------


def execute_run(ctx: ExecutionContext, run_id: uuid.UUID) -> ExecutionResult:
    token = claim_run(ctx, run_id)
    if token is None:
        logger.info("query_run_claim_skipped", extra={"run_ref": str(run_id)[:8]})
        return ExecutionResult("skipped")
    logger.info("query_run_claimed", extra={"run_ref": str(run_id)[:8], "worker": ctx.worker_name})
    try:
        with session_scope(ctx.session_factory) as db:
            run = db.get(QueryRun, run_id)
            assert run is not None
            snapshot: dict[str, Any] = run.parameters_snapshot
            connector_run_ids = list(
                db.scalars(
                    select(ConnectorRun.id)
                    .where(ConnectorRun.query_run_id == run_id)
                    .order_by(ConnectorRun.position)
                )
            )
        for connector_run_id in connector_run_ids:
            with session_scope(ctx.session_factory) as db:
                if _cancel_requested(db, run_id):
                    break
            _execute_connector(ctx, run_id, connector_run_id, token, snapshot)
        return ExecutionResult(_finalize(ctx, run_id, token))
    except LeaseLostError:
        logger.warning("query_run_lease_lost", extra={"run_ref": str(run_id)[:8]})
        return ExecutionResult("lease_lost")


# -- per connector -----------------------------------------------------------------------------


def _update_connector_run(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run_id: uuid.UUID,
    **values: Any,
) -> ConnectorRun:
    with session_scope(ctx.session_factory) as db:
        _renew(db, ctx, run_id, token)
        connector_run = db.get(ConnectorRun, connector_run_id, with_for_update=True)
        assert connector_run is not None
        for key, value in values.items():
            setattr(connector_run, key, value)
        db.flush()
        db.expunge(connector_run)
        return connector_run


def _finish_connector(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run: ConnectorRun,
    *,
    status: RunStatus,
    outcome: ConnectorOutcome | None,
    stopped_reason: str,
    note: str | None = None,
    error: ConnectorError | None = None,
) -> None:
    coverage = dict(connector_run.coverage)
    coverage.update(
        {"pages_completed": connector_run.pages_completed, "stopped_reason": stopped_reason}
    )
    values: dict[str, Any] = {
        "status": status,
        "outcome": outcome,
        "coverage": coverage,
        "coverage_note": note,
        "finished_at": utcnow(),
    }
    if error is not None:
        values.update(
            last_error_code=str(error.outcome),
            last_error_detail=error.detail[:500],
            retry_after_seconds=error.retry_after_seconds,
        )
    _update_connector_run(ctx, run_id, token, connector_run.id, **values)
    logger.info(
        "connector_run_finished",
        extra={
            "run_ref": str(run_id)[:8],
            "connector": connector_run.connector_id,
            "status": str(status),
            "outcome": str(outcome) if outcome else None,
        },
    )


def _execute_connector(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    connector_run_id: uuid.UUID,
    token: uuid.UUID,
    snapshot: dict[str, Any],
) -> None:
    with session_scope(ctx.session_factory) as db:
        connector_run = db.get(ConnectorRun, connector_run_id)
        assert connector_run is not None
        db.expunge(connector_run)
    if connector_run.status not in _UNFINISHED:
        return

    connector = get_connector(connector_run.connector_id)
    if connector is None or connector.descriptor.version != connector_run.connector_version:
        installed = connector.descriptor.version if connector else "none"
        _finish_connector(
            ctx,
            run_id,
            token,
            connector_run,
            status=RunStatus.FAILED,
            outcome=ConnectorOutcome.UNSUPPORTED,
            stopped_reason="connector_unavailable",
            note=(
                f"Snapshot requires {connector_run.connector_id} "
                f"{connector_run.connector_version}; installed version: {installed}."
            ),
        )
        return

    descriptor = connector.descriptor
    limits = snapshot.get("limits", {})
    max_pages = min(int(limits.get("max_pages", descriptor.max_pages)), descriptor.max_pages)
    max_items = min(
        int(limits.get("max_items_per_page", descriptor.max_items_per_page)),
        descriptor.max_items_per_page,
    )
    connector_run = _update_connector_run(
        ctx,
        run_id,
        token,
        connector_run.id,
        status=RunStatus.RUNNING,
        started_at=connector_run.started_at or utcnow(),
        coverage={
            **connector_run.coverage,
            "max_pages": max_pages,
            "max_items_per_page": max_items,
        },
    )
    deadline = time.monotonic() + descriptor.timeout_seconds
    page_delay = (
        ctx.settings.fixture_slow_page_delay_seconds
        if snapshot.get("parameters", {}).get("scenario") == "slow"
        else ctx.settings.fixture_page_delay_seconds
    )

    while True:
        with session_scope(ctx.session_factory) as db:
            cancel = _cancel_requested(db, run_id)
        if cancel:
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.CANCELED,
                outcome=ConnectorOutcome.CANCELED,
                stopped_reason="canceled",
                note="Canceled by the analyst; pages collected before cancellation are kept.",
            )
            return
        page_index = connector_run.pages_completed
        if page_index >= max_pages:
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.PARTIAL,
                outcome=ConnectorOutcome.PARTIAL,
                stopped_reason="page_limit",
                note=f"Stopped at the configured limit of {max_pages} page(s); more results exist.",
            )
            return
        if time.monotonic() > deadline:
            partial = connector_run.pages_completed > 0
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.PARTIAL if partial else RunStatus.FAILED,
                outcome=ConnectorOutcome.PARTIAL if partial else ConnectorOutcome.UNAVAILABLE,
                stopped_reason="timeout",
                note=f"Connector timeout of {descriptor.timeout_seconds}s reached.",
            )
            return

        attempts = dict(connector_run.page_attempts)
        attempt = int(attempts.get(str(page_index), 0)) + 1
        attempts[str(page_index)] = attempt
        connector_run = _update_connector_run(
            ctx,
            run_id,
            token,
            connector_run.id,
            page_attempts=attempts,
            fetch_attempts=connector_run.fetch_attempts + 1,
        )
        if page_delay:
            ctx.sleep(page_delay)

        try:
            page = connector.fetch_page(
                FetchRequest(
                    input_type=str(snapshot["input_type"]),
                    input_value=str(snapshot["input_value"]),
                    parameters=dict(snapshot.get("parameters", {})),
                    page_index=page_index,
                    attempt=attempt,
                    max_items_per_page=max_items,
                )
            )
        except ConnectorError as error:
            retryable = error.outcome in descriptor.retry_policy.retryable_outcomes
            if retryable and attempt < descriptor.retry_policy.max_attempts:
                connector_run = _update_connector_run(
                    ctx,
                    run_id,
                    token,
                    connector_run.id,
                    retries=connector_run.retries + 1,
                    last_error_code=str(error.outcome),
                    last_error_detail=error.detail[:500],
                    retry_after_seconds=error.retry_after_seconds,
                )
                wait = error.retry_after_seconds
                if wait is None:
                    wait = ctx.settings.fixture_retry_backoff_seconds * attempt
                ctx.sleep(
                    min(wait, MAX_RETRY_WAIT_SECONDS)
                    if ctx.settings.fixture_retry_backoff_seconds
                    else 0
                )
                continue
            collected = connector_run.pages_completed > 0
            exhausted = " after retries" if retryable else ""
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.PARTIAL if collected else RunStatus.FAILED,
                outcome=ConnectorOutcome.PARTIAL if collected else error.outcome,
                stopped_reason=str(error.outcome),
                note=(
                    f"Source outcome {error.outcome}{exhausted} on page {page_index + 1}; "
                    f"{connector_run.pages_completed} page(s) collected before the failure."
                    if collected
                    else f"Source outcome {error.outcome}{exhausted}; no data was collected."
                ),
                error=error,
            )
            return

        try:
            connector_run = _persist_page(
                ctx, run_id, token, connector_run, connector, page, snapshot
            )
        except CaseNotWritableError:
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.CANCELED,
                outcome=ConnectorOutcome.CANCELED,
                stopped_reason="case_not_writable",
                note="The case was archived or scheduled for deletion during the run.",
            )
            return
        if ctx.after_page is not None:
            ctx.after_page(connector_run.id, page_index)
        if not page.has_more:
            findings = connector_run.items_collected > 0
            _finish_connector(
                ctx,
                run_id,
                token,
                connector_run,
                status=RunStatus.COMPLETED,
                outcome=ConnectorOutcome.FINDINGS if findings else ConnectorOutcome.NO_FINDINGS,
                stopped_reason="complete",
                note=None if findings else "The lookup succeeded and returned no matches.",
            )
            return


# -- page persistence --------------------------------------------------------------------------


def _advisory_lock(db: Session, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _upsert_observed_account(
    db: Session, case_id: uuid.UUID, run_id: uuid.UUID, item: ObservedAccount
) -> uuid.UUID:
    """Find or create a candidate account by its stable platform ID (never by username)."""
    platform = normalize.normalize_platform(item.platform)
    _advisory_lock(db, f"account:{case_id}:{platform}:{item.source_object_id}")
    existing = db.scalar(
        select(EntityIdentifier.entity_id).where(
            EntityIdentifier.case_id == case_id,
            EntityIdentifier.identifier_type == IdentifierType.PLATFORM_ID,
            EntityIdentifier.platform == platform,
            EntityIdentifier.normalized_value == item.source_object_id,
        )
    )
    if existing is not None:
        return existing
    entity = Entity(
        id=uuid.uuid4(),
        case_id=case_id,
        entity_type=EntityType.PLATFORM_ACCOUNT,
        display_name=f"{item.username} on {item.platform}"[:300],
        description="Candidate account from synthetic fixture data. Not an identity assertion.",
        attributes={"synthetic": True, "candidate": True, "platform": item.platform},
        origin=Origin.OBSERVED,
        created_by_query_run_id=run_id,
    )
    db.add(entity)
    db.flush()
    db.add_all(
        [
            EntityIdentifier(
                case_id=case_id,
                entity_id=entity.id,
                identifier_type=IdentifierType.PLATFORM_ID,
                platform=platform,
                original_value=item.source_object_id,
                normalized_value=item.source_object_id,
            ),
            EntityIdentifier(
                case_id=case_id,
                entity_id=entity.id,
                identifier_type=IdentifierType.USERNAME,
                platform=platform,
                original_value=item.username,
                normalized_value=normalize.normalize_identifier(
                    IdentifierType.USERNAME, item.username
                ),
            ),
            EntityIdentifier(
                case_id=case_id,
                entity_id=entity.id,
                identifier_type=IdentifierType.URL,
                platform=platform,
                original_value=item.profile_reference,
                normalized_value=normalize.normalize_url(item.profile_reference),
            ),
        ]
    )
    db.flush()
    return entity.id


def _upsert_observed_domain(
    db: Session, case_id: uuid.UUID, run_id: uuid.UUID, domain: str
) -> uuid.UUID:
    normalized = normalize.normalize_domain(domain)
    _advisory_lock(db, f"domain:{case_id}:{normalized}")
    existing = db.scalar(
        select(Entity.id)
        .join(EntityIdentifier, EntityIdentifier.entity_id == Entity.id)
        .where(
            Entity.case_id == case_id,
            Entity.origin == Origin.OBSERVED,
            Entity.entity_type == EntityType.DOMAIN,
            EntityIdentifier.identifier_type == IdentifierType.DOMAIN,
            EntityIdentifier.normalized_value == normalized,
        )
    )
    if existing is not None:
        return existing
    entity = Entity(
        id=uuid.uuid4(),
        case_id=case_id,
        entity_type=EntityType.DOMAIN,
        display_name=normalized,
        description="Domain referenced by synthetic fixture data.",
        attributes={"synthetic": True},
        origin=Origin.OBSERVED,
        created_by_query_run_id=run_id,
    )
    db.add(entity)
    db.flush()
    db.add(
        EntityIdentifier(
            case_id=case_id,
            entity_id=entity.id,
            identifier_type=IdentifierType.DOMAIN,
            original_value=domain,
            normalized_value=normalized,
        )
    )
    db.flush()
    return entity.id


def _page_bytes(page: ConnectorPage) -> bytes:
    return json.dumps(page.raw_payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )


def _persist_page(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run: ConnectorRun,
    connector: Connector,
    page: ConnectorPage,
    snapshot: dict[str, Any],
) -> ConnectorRun:
    descriptor = connector.descriptor
    evidence_id = uuid.uuid4()
    case_id = connector_run.case_id
    key = EvidenceStorage.key_for(case_id, evidence_id)
    content = _page_bytes(page)
    db = ctx.session_factory()
    stored = False
    try:
        case = db.scalar(select(Case).where(Case.id == case_id).with_for_update(read=True))
        if case is None or case.status != CaseStatus.ACTIVE:
            raise CaseNotWritableError
        _renew(db, ctx, run_id, token)
        locked = db.get(ConnectorRun, connector_run.id, with_for_update=True)
        assert locked is not None
        already = db.scalar(
            select(EvidenceObject.id).where(
                EvidenceObject.connector_run_id == locked.id,
                EvidenceObject.page_index == page.page_index,
            )
        )
        if already is not None:
            db.commit()
            db.refresh(locked)
            db.expunge(locked)
            return locked

        run_number = db.scalar(select(QueryRun.run_number).where(QueryRun.id == run_id))
        now = utcnow()
        staged = ctx.storage.store(key, content)
        stored = True
        evidence = EvidenceObject(
            id=evidence_id,
            case_id=case_id,
            kind=EvidenceKind.JSON,
            title=(
                f"Synthetic fixture page {page.page_index + 1} "
                f"(run #{run_number}, {snapshot['input_type']})"
            )[:300],
            original_filename=None,
            content_type="application/json",
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
            storage_key=key,
            acquisition_method=AcquisitionMethod.SYNTHETIC_FIXTURE,
            source_reference=f"synthetic://{descriptor.connector_id}/runs/{run_id}/pages/{page.page_index}",
            collected_at=now,
            connector_id=descriptor.connector_id,
            connector_version=descriptor.version,
            query_run_id=run_id,
            connector_run_id=locked.id,
            page_index=page.page_index,
            description=SYNTHETIC_LABEL,
        )
        db.add(evidence)
        db.flush()

        for index, item in enumerate(page.items):
            account_id = _upsert_observed_account(db, case_id, run_id, item)
            observation_id = db.scalar(
                insert(Observation)
                .values(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    entity_id=account_id,
                    evidence_id=evidence_id,
                    query_run_id=run_id,
                    connector_run_id=locked.id,
                    observation_type="candidate_account",
                    source_object_id=item.source_object_id,
                    payload={
                        "synthetic": True,
                        "platform": item.platform,
                        "username": item.username,
                        "display_name": item.display_name,
                        "profile_reference": item.profile_reference,
                        "linked_domain": item.linked_domain,
                    },
                    collected_at=now,
                    event_time=_parse_time(item.event_time),
                    idempotency_key=f"{locked.id}:{page.page_index}:{index}",
                )
                .on_conflict_do_nothing(index_elements=["case_id", "idempotency_key"])
                .returning(Observation.id)
            )
            if observation_id is None:
                continue
            db.execute(
                insert(EntityEvidence)
                .values(
                    id=uuid.uuid4(), case_id=case_id, entity_id=account_id, evidence_id=evidence_id
                )
                .on_conflict_do_nothing(index_elements=["entity_id", "evidence_id"])
            )
            if item.linked_domain:
                domain_id = _upsert_observed_domain(db, case_id, run_id, item.linked_domain)
                db.execute(
                    insert(Relationship)
                    .values(
                        id=uuid.uuid4(),
                        case_id=case_id,
                        source_entity_id=account_id,
                        target_entity_id=domain_id,
                        predicate="links_to",
                        origin=Origin.OBSERVED,
                        review_status=ReviewStatus.UNREVIEWED,
                        description="Observed in synthetic fixture data.",
                        created_by_query_run_id=run_id,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            "case_id",
                            "source_entity_id",
                            "target_entity_id",
                            "predicate",
                        ],
                        index_where=text("origin = 'observed'"),
                    )
                )
                relationship_id = db.scalar(
                    select(Relationship.id).where(
                        Relationship.case_id == case_id,
                        Relationship.source_entity_id == account_id,
                        Relationship.target_entity_id == domain_id,
                        Relationship.predicate == "links_to",
                        Relationship.origin == Origin.OBSERVED,
                    )
                )
                db.execute(
                    insert(RelationshipEvidence)
                    .values(
                        id=uuid.uuid4(),
                        case_id=case_id,
                        relationship_id=relationship_id,
                        observation_id=observation_id,
                        evidence_id=evidence_id,
                        stance=Stance.SUPPORTS,
                    )
                    .on_conflict_do_nothing()
                )

        locked.pages_completed = page.page_index + 1
        locked.items_collected = locked.items_collected + len(page.items)
        locked.coverage = {
            **locked.coverage,
            "pages_completed": page.page_index + 1,
            "has_more": page.has_more,
        }
        db.commit()
        db.refresh(locked)
        db.expunge(locked)
        return locked
    except BaseException as exc:
        db.rollback()
        if stored:
            ctx.storage.remove_key(key)
        if isinstance(exc, DBAPIError):
            logger.error("page_persist_database_error", extra={"error_type": type(exc).__name__})
        raise
    finally:
        db.close()


# -- finalization ------------------------------------------------------------------------------


def _finalize(ctx: ExecutionContext, run_id: uuid.UUID, token: uuid.UUID) -> str:
    with session_scope(ctx.session_factory) as db:
        run = db.get(QueryRun, run_id, with_for_update=True)
        assert run is not None
        if run.lease_token != token or run.status != RunStatus.RUNNING:
            raise LeaseLostError
        now = utcnow()
        connector_runs = list(
            db.scalars(select(ConnectorRun).where(ConnectorRun.query_run_id == run_id))
        )
        for connector_run in connector_runs:
            if connector_run.status in _UNFINISHED:
                connector_run.status = RunStatus.CANCELED
                connector_run.outcome = ConnectorOutcome.CANCELED
                connector_run.finished_at = now
        statuses = {cr.status for cr in connector_runs}
        if run.cancel_requested_at is not None or RunStatus.CANCELED in statuses:
            final = RunStatus.CANCELED
        elif statuses == {RunStatus.COMPLETED}:
            final = RunStatus.COMPLETED
        elif statuses == {RunStatus.FAILED}:
            final = RunStatus.FAILED
        else:
            final = RunStatus.PARTIAL
        run.status = final
        run.finished_at = now
        run.lease_token = None
        run.lease_expires_at = None
        dispatch.mark_done(db, AggregateType.QUERY_RUN, run_id)
    logger.info("query_run_finished", extra={"run_ref": str(run_id)[:8], "status": str(final)})
    return str(final)
