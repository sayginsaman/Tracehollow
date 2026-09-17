"""Run one processing job: claim it, call the handler for its type, record the outcome."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from app.imports import jobs, whatsapp_job
from app.imports.models import ProcessingJobType, ProcessingStatus

logger = logging.getLogger(__name__)

Handler = Callable[[jobs.ProcessingContext, jobs.JobSnapshot, uuid.UUID], str]


def _handlers() -> dict[str, Handler]:
    from app.imports import documents_job

    return {
        ProcessingJobType.WHATSAPP_EXPORT: whatsapp_job.process,
        ProcessingJobType.DOCUMENT_TEXT: documents_job.process,
    }


def execute_job(ctx: jobs.ProcessingContext, job_id: uuid.UUID) -> str:
    claimed = jobs.claim(ctx, job_id)
    if claimed is None:
        return "skipped"
    token, job = claimed
    log_extra = {"job_ref": str(job.id)[:8], "job_type": job.job_type, "attempt": job.attempts}
    handler = _handlers().get(job.job_type)
    if handler is None:
        return jobs.finish_without_results(
            ctx,
            job.id,
            token,
            ProcessingStatus.FAILED,
            error_code="unsupported_job_type",
            error_detail=f"No handler for job type {job.job_type}.",
        )
    try:
        outcome = handler(ctx, job, token)
    except jobs.LeaseLostError:
        logger.warning("processing_job_lease_lost", extra=log_extra)
        return "lease_lost"
    except jobs.JobCanceledError:
        logger.info("processing_job_canceled", extra=log_extra)
        return jobs.finish_without_results(
            ctx,
            job.id,
            token,
            ProcessingStatus.CANCELED,
            error_code="canceled",
            error_detail="Processing was stopped before results were recorded.",
        )
    except jobs.ProcessingError as exc:
        logger.info(
            "processing_job_error",
            extra={**log_extra, "error_code": exc.code, "retryable": exc.retryable},
        )
        if exc.retryable:
            return jobs.requeue_after_retryable_error(ctx, job.id, token, exc)
        return jobs.finish_without_results(
            ctx,
            job.id,
            token,
            ProcessingStatus.FAILED,
            error_code=exc.code,
            error_detail=exc.detail,
        )
    except Exception as exc:
        logger.error(
            "processing_job_crashed", extra={**log_extra, "error_type": type(exc).__name__}
        )
        return jobs.requeue_after_retryable_error(
            ctx,
            job.id,
            token,
            jobs.ProcessingError(
                "internal_error",
                f"Processing stopped unexpectedly ({type(exc).__name__}); it is retried while "
                "attempts remain.",
                retryable=True,
            ),
        )
    logger.info("processing_job_finished", extra={**log_extra, "outcome": outcome})
    return outcome
