"""Query execution engine.

Delivery is at-least-once. Correctness comes from PostgreSQL, not from the broker:

* A worker must *claim* a run (queued, or running with an expired lease) with a conditional
  update that issues a fresh lease token. A duplicate message cannot claim a run that another
  worker holds, and a finished run cannot be claimed at all.
* Work is split into bounded pages. Each page is persisted in one transaction together with
  its evidence files, observations, entities, relationships, progress counters and the cursor
  for the next page. A page that already has evidence for ``(connector_run_id, page_index)`` is
  skipped and observation idempotency keys make re-inserts no-ops, so redelivery or lease
  takeover never duplicates logical results.
* Every page commit renews the lease only if the worker still holds the token.
* Cancellation is read from the database before each page, during retry waits and, through the
  fetch context, while a connector waits on the network or a subprocess.
* Per-connector concurrency slots and per-source request pacing are shared by all workers
  (``app.connectors.limits``).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ai import indexing
from app.cases.models import Case, CaseStatus
from app.config import Settings
from app.connectors import limits, netguard
from app.connectors.base import (
    CollectionMode,
    Connector,
    ConnectorError,
    ConnectorPage,
    EntityDraft,
    FetchContext,
    FetchRequest,
    effective_collection_mode,
)
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
    IdentifierType,
    Observation,
    Origin,
    Relationship,
    RelationshipEvidence,
    ReviewStatus,
    Stance,
)
from app.evidence.models import AcquisitionMethod, EvidenceObject
from app.evidence.storage import EvidenceStorage
from app.integrations import crypto
from app.integrations import service as integrations
from app.queries.models import ConnectorOutcome, ConnectorRun, QueryRun, RunStatus

logger = logging.getLogger(__name__)

MAX_RETRY_WAIT_SECONDS = 30.0
PROGRESS_INTERVAL_SECONDS = 2.0
CANCEL_CHECK_INTERVAL_SECONDS = 1.0
SLOT_POLL_SECONDS = 2.0
_UNFINISHED = (RunStatus.QUEUED, RunStatus.RUNNING)
_SUCCESS_OUTCOMES = (ConnectorOutcome.FINDINGS, ConnectorOutcome.NO_FINDINGS)
_INDEXABLE_KINDS = ("text", "json")


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
    # Test hooks: replace network access and DNS resolution for HTTP-based connectors.
    http_transport: httpx2.BaseTransport | None = None
    resolver: Callable[[str, int], list[str]] | None = None


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
                        QueryRun.claim_count < ctx.settings.run_max_claims,
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


def _abandon_if_exhausted(ctx: ExecutionContext, run_id: uuid.UUID) -> bool:
    """Fail a run whose lease expired after the maximum number of claims (workers keep dying)."""
    now = utcnow()
    with session_scope(ctx.session_factory) as db:
        run = db.scalar(
            select(QueryRun)
            .where(
                QueryRun.id == run_id,
                QueryRun.status == RunStatus.RUNNING,
                QueryRun.lease_expires_at < now,
                QueryRun.claim_count >= ctx.settings.run_max_claims,
            )
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return False
        _close_unfinished_connectors(
            db,
            run_id,
            error_code="worker_lost",
            note=(
                f"The execution was interrupted {run.claim_count} times without finishing; "
                "it was stopped so the failure is visible. Pages collected before are kept."
            ),
        )
        run.error_code = "worker_lost"
        _apply_final_status(db, run)
    logger.error("query_run_abandoned", extra={"run_ref": str(run_id)[:8]})
    return True


def _close_unfinished_connectors(
    db: Session, run_id: uuid.UUID, *, error_code: str, note: str, detail: str | None = None
) -> None:
    now = utcnow()
    for connector_run in db.scalars(
        select(ConnectorRun).where(
            ConnectorRun.query_run_id == run_id, ConnectorRun.status.in_(_UNFINISHED)
        )
    ):
        collected = connector_run.pages_completed > 0
        connector_run.status = RunStatus.PARTIAL if collected else RunStatus.FAILED
        connector_run.outcome = ConnectorOutcome.PARTIAL if collected else None
        connector_run.last_error_code = error_code
        connector_run.last_error_detail = detail
        connector_run.coverage_note = note
        connector_run.coverage = {
            **connector_run.coverage,
            "pages_completed": connector_run.pages_completed,
            "stopped_reason": error_code,
        }
        connector_run.finished_at = now


def _fail_internal(
    ctx: ExecutionContext, run_id: uuid.UUID, token: uuid.UUID, exc: Exception
) -> str:
    with session_scope(ctx.session_factory) as db:
        run = db.get(QueryRun, run_id, with_for_update=True)
        if run is None or run.lease_token != token or run.status != RunStatus.RUNNING:
            return "lease_lost"
        _close_unfinished_connectors(
            db,
            run_id,
            error_code="internal_error",
            note=(
                "Tracehollow hit an internal error while executing this connector; the source "
                "outcome is unknown. Pages collected before the error are kept."
            ),
            detail=type(exc).__name__,
        )
        run.error_code = "internal_error"
        return _apply_final_status(db, run)


def execute_run(ctx: ExecutionContext, run_id: uuid.UUID) -> ExecutionResult:
    token = claim_run(ctx, run_id)
    if token is None:
        if _abandon_if_exhausted(ctx, run_id):
            return ExecutionResult("failed")
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
    except (OperationalError, InterfaceError):
        # Database connectivity is transient: keep the lease so the run is recovered later.
        raise
    except Exception as exc:
        logger.exception(
            "query_run_internal_error",
            extra={"run_ref": str(run_id)[:8], "error_type": type(exc).__name__},
        )
        return ExecutionResult(_fail_internal(ctx, run_id, token, exc))


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


def _status_for(outcome: ConnectorOutcome, *, incomplete: bool, items: int) -> RunStatus:
    if outcome in _SUCCESS_OUTCOMES:
        return RunStatus.PARTIAL if incomplete else RunStatus.COMPLETED
    if outcome == ConnectorOutcome.PARTIAL:
        return RunStatus.PARTIAL
    if outcome == ConnectorOutcome.CANCELED:
        return RunStatus.CANCELED
    return RunStatus.PARTIAL if items > 0 else RunStatus.FAILED


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
    coverage.pop("progress", None)
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
            last_error_code=(error.code or str(error.outcome))[:64],
            last_error_detail=error.detail[:500],
            retry_after_seconds=error.retry_after_seconds,
        )
        if error.quota is not None:
            values["quota_usage"] = error.quota
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


def _network_policy(ctx: ExecutionContext) -> netguard.NetworkPolicy:
    settings = ctx.settings
    return netguard.NetworkPolicy(
        allowed_ports=frozenset(settings.collection_allowed_ports),
        allowed_private_networks=netguard.parse_networks(
            settings.collection_allowed_private_networks
        ),
        resolver=ctx.resolver or netguard.system_resolver,
    )


class _RunSignals:
    """Throttled cancellation checks and progress writes for one connector run."""

    def __init__(
        self,
        ctx: ExecutionContext,
        run_id: uuid.UUID,
        token: uuid.UUID,
        connector_run_id: uuid.UUID,
    ) -> None:
        self.ctx = ctx
        self.run_id = run_id
        self.token = token
        self.connector_run_id = connector_run_id
        self._cancel_checked = 0.0
        self._cancel = False
        self._progress_written = 0.0
        self.credential_results: dict[str, str] = {}

    def cancelled(self, *, force: bool = False) -> bool:
        if self._cancel:
            return True
        now = time.monotonic()
        if force or now - self._cancel_checked >= CANCEL_CHECK_INTERVAL_SECONDS:
            self._cancel_checked = now
            with session_scope(self.ctx.session_factory) as db:
                self._cancel = _cancel_requested(db, self.run_id)
        return self._cancel

    def progress(self, values: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._progress_written < PROGRESS_INTERVAL_SECONDS:
            return
        self._progress_written = now
        with session_scope(self.ctx.session_factory) as db:
            _renew(db, self.ctx, self.run_id, self.token)
            connector_run = db.get(ConnectorRun, self.connector_run_id, with_for_update=True)
            assert connector_run is not None
            connector_run.coverage = {**connector_run.coverage, "progress": dict(values)}


def _fetch_context(
    ctx: ExecutionContext,
    signals: _RunSignals,
    connector: Connector,
    deadline: float,
) -> FetchContext:
    descriptor = connector.descriptor
    settings = ctx.settings

    def credential(name: str) -> str | None:
        with session_scope(ctx.session_factory) as db:
            try:
                return integrations.read_plaintext(db, settings, descriptor.connector_id, name)
            except (crypto.CredentialDecryptionError, crypto.CredentialStoreUnavailableError):
                raise ConnectorError(
                    ConnectorOutcome.AUTHENTICATION_REQUIRED,
                    "The stored credential cannot be decrypted with the configured key; "
                    "set it again on the Sources page.",
                    code="credential_unreadable",
                ) from None

    def credential_result(name: str, result: str) -> None:
        signals.credential_results[name] = result

    def pace(key: str, interval: float) -> None:
        limits.pace(
            ctx.session_factory,
            f"{descriptor.connector_id}:{key}",
            interval,
            sleep=ctx.sleep,
            cancelled=signals.cancelled,
        )

    context = FetchContext(
        network_policy=_network_policy(ctx),
        cancelled=signals.cancelled,
        progress=signals.progress,
        credential=credential,
        pace=pace,
        deadline=deadline,
        max_response_bytes=settings.collection_max_response_bytes,
        request_timeout_seconds=settings.collection_request_timeout_seconds,
        max_redirects=settings.collection_max_redirects,
        user_agent=settings.collection_user_agent,
        http_transport=ctx.http_transport,
        settings=settings,
        credential_result=credential_result,
    )
    return context


def _wait_for_slot(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run: ConnectorRun,
    connector: Connector,
    signals: _RunSignals,
    deadline: float,
) -> bool:
    descriptor = connector.descriptor
    waited = False
    while True:
        slot = limits.try_claim_slot(
            ctx.session_factory,
            descriptor.connector_id,
            descriptor.max_concurrent_runs,
            connector_run.id,
            ctx.settings.collection_slot_lease_seconds,
        )
        if slot is not None:
            return True
        if signals.cancelled() or time.monotonic() > deadline:
            return False
        if not waited:
            waited = True
            _update_connector_run(
                ctx,
                run_id,
                token,
                connector_run.id,
                coverage={
                    **connector_run.coverage,
                    "progress": {"waiting_for_slot": True, "limit": descriptor.max_concurrent_runs},
                },
            )
        ctx.sleep(SLOT_POLL_SECONDS)


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
    deadline = time.monotonic() + descriptor.timeout_seconds
    signals = _RunSignals(ctx, run_id, token, connector_run.id)
    if not _wait_for_slot(ctx, run_id, token, connector_run, connector, signals, deadline):
        canceled = signals.cancelled()
        _finish_connector(
            ctx,
            run_id,
            token,
            connector_run,
            status=RunStatus.CANCELED if canceled else RunStatus.FAILED,
            outcome=ConnectorOutcome.CANCELED if canceled else ConnectorOutcome.UNAVAILABLE,
            stopped_reason="canceled" if canceled else "concurrency_limit",
            note=(
                "Canceled while waiting for a free collection slot."
                if canceled
                else (
                    f"No collection slot became free within {descriptor.timeout_seconds}s "
                    f"(at most {descriptor.max_concurrent_runs} concurrent runs of this source)."
                )
            ),
        )
        return
    try:
        _run_pages(ctx, run_id, token, connector_run, connector, snapshot, signals, deadline)
    finally:
        limits.release_slot(ctx.session_factory, descriptor.connector_id, connector_run.id)
        if signals.credential_results:
            with session_scope(ctx.session_factory) as db:
                for name, result in signals.credential_results.items():
                    integrations.record_result(db, descriptor.connector_id, name, result)


def _retry_wait(
    ctx: ExecutionContext, connector: Connector, error: ConnectorError, attempt: int
) -> float:
    policy = connector.descriptor.retry_policy
    if connector.descriptor.synthetic:
        if not ctx.settings.fixture_retry_backoff_seconds:
            return 0.0
        wait = error.retry_after_seconds
        if wait is None:
            wait = ctx.settings.fixture_retry_backoff_seconds * attempt
        return min(wait, MAX_RETRY_WAIT_SECONDS)
    if error.retry_after_seconds is not None:
        return float(error.retry_after_seconds)
    backoff = policy.base_backoff_seconds * (2 ** (attempt - 1))
    return float(min(backoff, policy.max_backoff_seconds))


def _run_pages(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run: ConnectorRun,
    connector: Connector,
    snapshot: dict[str, Any],
    signals: _RunSignals,
    deadline: float,
) -> None:
    descriptor = connector.descriptor
    limits_snapshot = snapshot.get("limits", {})
    max_pages = min(
        int(limits_snapshot.get("max_pages", descriptor.max_pages)), descriptor.max_pages
    )
    max_items = min(
        int(limits_snapshot.get("max_items_per_page", descriptor.max_items_per_page)),
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
            **{k: v for k, v in connector_run.coverage.items() if k != "progress"},
            "max_pages": max_pages,
            "max_items_per_page": max_items,
        },
    )
    page_delay = (
        (
            ctx.settings.fixture_slow_page_delay_seconds
            if snapshot.get("parameters", {}).get("scenario") == "slow"
            else ctx.settings.fixture_page_delay_seconds
        )
        if descriptor.synthetic
        else 0.0
    )

    while True:
        if signals.cancelled(force=True):
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

        cursor = connector_run.coverage.get("next_cursor")
        try:
            page = connector.fetch_page(
                FetchRequest(
                    input_type=str(snapshot["input_type"]),
                    input_value=str(snapshot["input_value"]),
                    parameters=dict(snapshot.get("parameters", {})),
                    page_index=page_index,
                    attempt=attempt,
                    max_items_per_page=max_items,
                    cursor=dict(cursor) if isinstance(cursor, dict) else None,
                    context=_fetch_context(ctx, signals, connector, deadline),
                )
            )
        except ConnectorError as error:
            if error.outcome == ConnectorOutcome.CANCELED:
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
            retryable = error.outcome in descriptor.retry_policy.retryable_outcomes
            wait = _retry_wait(ctx, connector, error, attempt) if retryable else 0.0
            too_long = (
                not descriptor.synthetic and wait > ctx.settings.collection_max_retry_wait_seconds
            )
            if (
                retryable
                and not too_long
                and attempt < descriptor.retry_policy.max_attempts
                and time.monotonic() + wait < deadline
            ):
                connector_run = _update_connector_run(
                    ctx,
                    run_id,
                    token,
                    connector_run.id,
                    retries=connector_run.retries + 1,
                    last_error_code=(error.code or str(error.outcome))[:64],
                    last_error_detail=error.detail[:500],
                    retry_after_seconds=error.retry_after_seconds,
                    **({"quota_usage": error.quota} if error.quota is not None else {}),
                )
                _sleep_unless_cancelled(ctx, signals, wait)
                continue
            collected = connector_run.pages_completed > 0
            exhausted = (
                " (the source asked to wait longer than allowed)"
                if too_long
                else " after retries"
                if retryable
                else ""
            )
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
                ctx,
                run_id,
                token,
                connector_run,
                connector,
                page,
                dict(snapshot.get("parameters", {})),
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
            _finish_after_last_page(ctx, run_id, token, connector_run)
            return


def _sleep_unless_cancelled(ctx: ExecutionContext, signals: _RunSignals, seconds: float) -> None:
    remaining = seconds
    while remaining > 0 and not signals.cancelled():
        step = min(remaining, 1.0)
        ctx.sleep(step)
        remaining -= step


def _finish_after_last_page(
    ctx: ExecutionContext, run_id: uuid.UUID, token: uuid.UUID, connector_run: ConnectorRun
) -> None:
    coverage = connector_run.coverage
    incomplete: list[str] = list(coverage.get("incomplete_reasons", []))
    hint = coverage.get("outcome_hint")
    items = connector_run.items_collected
    if hint:
        outcome = ConnectorOutcome(hint)
    else:
        outcome = ConnectorOutcome.FINDINGS if items > 0 else ConnectorOutcome.NO_FINDINGS
    status = _status_for(outcome, incomplete=bool(incomplete), items=items)
    if status == RunStatus.PARTIAL:
        outcome = ConnectorOutcome.PARTIAL
    notes = list(coverage.get("notes", []))
    if incomplete:
        note: str | None = "Incomplete: " + " ".join(incomplete)
    elif outcome == ConnectorOutcome.NO_FINDINGS:
        note = "The lookup succeeded and returned no matches."
    elif outcome in _SUCCESS_OUTCOMES:
        note = None
    else:
        note = f"Source outcome {outcome}; see the stored evidence for what was attempted."
    if notes:
        note = " ".join(filter(None, [note, *notes]))
    code = coverage.get("outcome_code") if hint else None
    _finish_connector(
        ctx,
        run_id,
        token,
        connector_run,
        status=status,
        outcome=outcome,
        stopped_reason="complete",
        note=note,
        error=ConnectorError(outcome, note or str(outcome), code=str(code))
        if code and outcome not in (*_SUCCESS_OUTCOMES, ConnectorOutcome.PARTIAL)
        else None,
    )


# -- page persistence --------------------------------------------------------------------------


def _advisory_lock(db: Session, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def _normalized(identifier_type: str, value: str) -> str:
    return normalize.normalize_identifier(IdentifierType(identifier_type), value)


def _upsert_entity(
    db: Session, case_id: uuid.UUID, run_id: uuid.UUID, draft: EntityDraft
) -> uuid.UUID:
    """Find the entity for the draft's match identifier, or create an observed entity.

    Never merges on names. A platform ID matches the account entity whoever created it; other
    identifiers only match observed entities of the same type, so analyst-created entities are
    not modified. Raises ``IdentifierError`` before writing anything when an identifier cannot
    be normalized.
    """
    match = draft.match
    platform = normalize.normalize_platform(match.platform)
    normalized_value = _normalized(match.identifier_type, match.value)
    identifiers: list[tuple[str, str | None, str, str]] = []
    for identifier in draft.identifiers or (match,):
        row = (
            identifier.identifier_type,
            normalize.normalize_platform(identifier.platform),
            _normalized(identifier.identifier_type, identifier.value),
            identifier.value[:2048],
        )
        if all(existing[:3] != row[:3] for existing in identifiers):
            identifiers.append(row)
    _advisory_lock(
        db,
        f"entity:{case_id}:{draft.entity_type}:{match.identifier_type}:{platform}:"
        f"{normalized_value}",
    )
    conditions = [
        Entity.case_id == case_id,
        EntityIdentifier.identifier_type == match.identifier_type,
        EntityIdentifier.normalized_value == normalized_value,
    ]
    if match.identifier_type != IdentifierType.PLATFORM_ID:
        # A stable platform ID identifies the account whoever recorded it (and is unique per
        # case); any other identifier only matches entities that collection created.
        conditions += [Entity.origin == Origin.OBSERVED, Entity.entity_type == draft.entity_type]
    conditions.append(
        EntityIdentifier.platform == platform
        if platform is not None
        else EntityIdentifier.platform.is_(None)
    )
    existing = db.scalar(
        select(Entity.id)
        .join(EntityIdentifier, EntityIdentifier.entity_id == Entity.id)
        .where(*conditions)
        .limit(1)
    )
    if existing is not None:
        return existing
    entity = Entity(
        id=uuid.uuid4(),
        case_id=case_id,
        entity_type=draft.entity_type,
        display_name=draft.display_name[:300],
        description=draft.description,
        attributes=draft.attributes,
        origin=Origin.OBSERVED,
        created_by_query_run_id=run_id,
    )
    db.add(entity)
    db.flush()
    for identifier_type, identifier_platform, value, original in identifiers:
        db.add(
            EntityIdentifier(
                case_id=case_id,
                entity_id=entity.id,
                identifier_type=identifier_type,
                platform=identifier_platform,
                original_value=original,
                normalized_value=value,
            )
        )
    db.flush()
    return entity.id


def _persist_page(
    ctx: ExecutionContext,
    run_id: uuid.UUID,
    token: uuid.UUID,
    connector_run: ConnectorRun,
    connector: Connector,
    page: ConnectorPage,
    parameters: dict[str, Any],
) -> ConnectorRun:
    descriptor = connector.descriptor
    collection_mode = effective_collection_mode(descriptor, parameters)
    case_id = connector_run.case_id
    evidence_ids = {draft.key: uuid.uuid4() for draft in page.evidence}
    stored_keys: list[str] = []
    db = ctx.session_factory()
    try:
        case = db.scalar(select(Case).where(Case.id == case_id).with_for_update(read=True))
        if case is None or case.status != CaseStatus.ACTIVE:
            raise CaseNotWritableError
        _renew(db, ctx, run_id, token)
        locked = db.get(ConnectorRun, connector_run.id, with_for_update=True)
        assert locked is not None
        already = db.scalar(
            select(EvidenceObject.id)
            .where(
                EvidenceObject.connector_run_id == locked.id,
                EvidenceObject.page_index == page.page_index,
            )
            .limit(1)
        )
        if already is not None or locked.pages_completed > page.page_index:
            db.commit()
            db.refresh(locked)
            db.expunge(locked)
            return locked

        run_number = db.scalar(select(QueryRun.run_number).where(QueryRun.id == run_id))
        now = utcnow()
        acquisition = (
            AcquisitionMethod.SYNTHETIC_FIXTURE
            if descriptor.synthetic
            else AcquisitionMethod.CONNECTOR_COLLECTION
        )
        for draft in page.evidence:
            evidence_id = evidence_ids[draft.key]
            key = EvidenceStorage.key_for(case_id, evidence_id)
            staged = ctx.storage.store(key, draft.content)
            stored_keys.append(key)
            title = draft.title.replace("{run_number}", str(run_number)).replace(
                "{run_id}", str(run_id)
            )
            reference = draft.source_reference.replace("{run_id}", str(run_id))
            db.add(
                EvidenceObject(
                    id=evidence_id,
                    case_id=case_id,
                    kind=draft.kind,
                    title=title[:300],
                    original_filename=None,
                    content_type=draft.content_type[:100],
                    size_bytes=staged.size_bytes,
                    sha256=staged.sha256,
                    storage_key=key,
                    acquisition_method=acquisition,
                    source_reference=reference[:2048],
                    source_published_at=draft.source_published_at,
                    source_published_at_original=draft.source_published_at_original,
                    collected_at=now,
                    connector_id=descriptor.connector_id,
                    connector_version=descriptor.version,
                    query_run_id=run_id,
                    connector_run_id=locked.id,
                    page_index=page.page_index,
                    page_part=draft.key[:32],
                    description=draft.description,
                    collection_mode=(
                        None
                        if collection_mode == CollectionMode.SYNTHETIC_FIXTURE
                        else str(collection_mode)
                    ),
                    access_category=None if descriptor.synthetic else draft.access_category,
                    derived_from_evidence_id=(
                        evidence_ids[draft.derived_from] if draft.derived_from else None
                    ),
                    collection_metadata=draft.collection_metadata,
                )
            )
        db.flush()
        for draft in page.evidence:
            if (
                draft.indexable
                and draft.kind in _INDEXABLE_KINDS
                and not any(other.derived_from == draft.key for other in page.evidence)
            ):
                indexing.mark_evidence_for_indexing(
                    db,
                    ctx.settings,
                    case_id=case_id,
                    evidence_id=evidence_ids[draft.key],
                    ai_mode=case.ai_mode,
                )

        entity_ids: dict[str, uuid.UUID] = {}
        skipped_entities = 0
        for entity_draft in page.entities:
            try:
                entity_ids[entity_draft.key] = _upsert_entity(db, case_id, run_id, entity_draft)
            except normalize.IdentifierError:
                # The evidence and observation are kept; only the entity link is omitted.
                skipped_entities += 1
        observation_ids: dict[int, uuid.UUID] = {}
        inserted = 0
        repeated = 0
        for index, observation in enumerate(page.observations):
            evidence_id = evidence_ids[observation.evidence_key]
            entity_id = entity_ids.get(observation.entity_key) if observation.entity_key else None
            suffix = observation.idempotency_suffix or str(index)
            observation_id = db.scalar(
                insert(Observation)
                .values(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    entity_id=entity_id,
                    evidence_id=evidence_id,
                    query_run_id=run_id,
                    connector_run_id=locked.id,
                    observation_type=observation.observation_type,
                    source_object_id=(observation.source_object_id or "")[:512] or None,
                    payload=observation.payload,
                    collected_at=now,
                    event_time=observation.event_time,
                    source_published_at=observation.source_published_at,
                    idempotency_key=(
                        f"{locked.id}:run:{suffix}"
                        if observation.dedupe_across_pages
                        else f"{locked.id}:{page.page_index}:{suffix}"
                    )[:200],
                )
                .on_conflict_do_nothing(index_elements=["case_id", "idempotency_key"])
                .returning(Observation.id)
            )
            if observation_id is None:
                if observation.dedupe_across_pages:
                    repeated += 1
                continue
            inserted += 1
            observation_ids[index] = observation_id
            if entity_id is not None:
                db.execute(
                    insert(EntityEvidence)
                    .values(
                        id=uuid.uuid4(),
                        case_id=case_id,
                        entity_id=entity_id,
                        evidence_id=evidence_id,
                    )
                    .on_conflict_do_nothing(index_elements=["entity_id", "evidence_id"])
                )

        for relationship in page.relationships:
            supporting = observation_ids.get(relationship.observation_index)
            if supporting is None:
                continue
            source_id = entity_ids.get(relationship.source_key)
            target_id = entity_ids.get(relationship.target_key)
            if source_id is None or target_id is None or source_id == target_id:
                continue
            origin = Origin(relationship.origin)
            db.execute(
                insert(Relationship)
                .values(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    source_entity_id=source_id,
                    target_entity_id=target_id,
                    predicate=relationship.predicate,
                    origin=origin,
                    review_status=ReviewStatus.UNREVIEWED,
                    description=relationship.description,
                    created_by_query_run_id=run_id,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["case_id", "source_entity_id", "target_entity_id", "predicate"],
                    index_where=text("origin = 'observed'"),
                )
            )
            relationship_id = db.scalar(
                select(Relationship.id)
                .where(
                    Relationship.case_id == case_id,
                    Relationship.source_entity_id == source_id,
                    Relationship.target_entity_id == target_id,
                    Relationship.predicate == relationship.predicate,
                    Relationship.origin == origin,
                )
                .limit(1)
            )
            supporting_evidence = page.observations[relationship.observation_index].evidence_key
            db.execute(
                insert(RelationshipEvidence)
                .values(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    relationship_id=relationship_id,
                    observation_id=supporting,
                    evidence_id=evidence_ids[supporting_evidence],
                    stance=Stance.SUPPORTS,
                )
                .on_conflict_do_nothing()
            )

        coverage = {k: v for k, v in locked.coverage.items() if k != "progress"}
        coverage.update(page.coverage)
        coverage["pages_completed"] = page.page_index + 1
        coverage["has_more"] = page.has_more
        coverage["next_cursor"] = page.next_cursor
        duplicates = len(page.observations) - inserted
        if duplicates:
            coverage["duplicate_observations_skipped"] = (
                int(coverage.get("duplicate_observations_skipped", 0)) + duplicates
            )
        if page.incomplete_reason:
            coverage["incomplete_reasons"] = [
                *coverage.get("incomplete_reasons", []),
                page.incomplete_reason,
            ]
        notes = list(page.notes)
        if skipped_entities:
            notes.append(
                f"{skipped_entities} referenced entit{'y' if skipped_entities == 1 else 'ies'} "
                "had identifiers that could not be normalized and were not created."
            )
        if notes:
            coverage["notes"] = [*coverage.get("notes", []), *notes]
        if page.outcome_hint is not None:
            coverage["outcome_hint"] = str(page.outcome_hint)
            if page.outcome_code:
                coverage["outcome_code"] = page.outcome_code[:64]
        locked.pages_completed = page.page_index + 1
        locked.items_collected = locked.items_collected + max(0, page.items - repeated)
        locked.coverage = coverage
        if page.quota is not None:
            locked.quota_usage = page.quota
        db.commit()
        db.refresh(locked)
        db.expunge(locked)
        return locked
    except BaseException as exc:
        db.rollback()
        for key in stored_keys:
            ctx.storage.remove_key(key)
        if isinstance(exc, DBAPIError):
            logger.error("page_persist_database_error", extra={"error_type": type(exc).__name__})
        raise
    finally:
        db.close()


def page_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


# -- finalization ------------------------------------------------------------------------------


def _apply_final_status(db: Session, run: QueryRun) -> str:
    now = utcnow()
    connector_runs = list(
        db.scalars(select(ConnectorRun).where(ConnectorRun.query_run_id == run.id))
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
    dispatch.mark_done(db, AggregateType.QUERY_RUN, run.id)
    logger.info("query_run_finished", extra={"run_ref": str(run.id)[:8], "status": str(final)})
    return str(final)


def _finalize(ctx: ExecutionContext, run_id: uuid.UUID, token: uuid.UUID) -> str:
    with session_scope(ctx.session_factory) as db:
        run = db.get(QueryRun, run_id, with_for_update=True)
        assert run is not None
        if run.lease_token != token or run.status != RunStatus.RUNNING:
            raise LeaseLostError
        return _apply_final_status(db, run)
