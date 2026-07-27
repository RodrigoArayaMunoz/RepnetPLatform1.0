import asyncio
import os
import time
import uuid
from contextlib import suppress

from fastapi import HTTPException
from celery.utils.log import get_task_logger

from celery_app import celery_app
from config import settings
from services.job_store import JobStore
from services.ml_client import ml_client
from services.publication_export_service import publication_export_service
from services.publication_export_store import publication_export_store

logger = get_task_logger(__name__)


def _is_retryable_export_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        return exc.status_code not in {400, 401, 403, 404, 410}
    if isinstance(exc, ValueError):
        return False
    return True


def _retry_delay_seconds(retry_number: int) -> int:
    base_delay = int(
        settings.ml_publication_export_retry_base_delay_seconds
    )
    max_delay = int(
        settings.ml_publication_export_retry_max_delay_seconds
    )
    return min(max_delay, base_delay * (2 ** max(0, retry_number - 1)))


@celery_app.task(
    bind=True,
    name="tasks.export_publications_job",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=settings.ml_publication_export_task_max_retries,
)
def export_publications_task(
    task,
    job_id: str,
    user_id: str,
    creation_date: str,
) -> None:
    job = JobStore.get(job_id)
    if not job:
        logger.warning(
            "[TASK PUBLICATION_EXPORT][MISSING_JOB] job_id=%s",
            job_id,
        )
        return

    requested_by_user_id = str(
        job.get("requested_by_user_id") or ""
    )
    result_path = str(job.get("result_path") or "")
    if (
        job.get("status") == "success"
        and result_path
        and os.path.exists(result_path)
    ):
        logger.warning(
            "[TASK PUBLICATION_EXPORT][SKIP_COMPLETED] "
            "job_id=%s task_id=%s",
            job_id,
            task.request.id,
        )
        return

    if job.get("status") == "error":
        logger.warning(
            "[TASK PUBLICATION_EXPORT][SKIP_TERMINAL_ERROR] "
            "job_id=%s task_id=%s",
            job_id,
            task.request.id,
        )
        return

    lock_owner = str(task.request.id or uuid.uuid4())
    if not publication_export_store.try_acquire_run_lock(
        job_id=job_id,
        owner=lock_owner,
    ):
        delivery_info = getattr(task.request, "delivery_info", {}) or {}
        if delivery_info.get("redelivered"):
            lock_ttl = publication_export_store.run_lock_ttl(job_id)
            countdown = max(5, lock_ttl + 2)
            logger.warning(
                "[TASK PUBLICATION_EXPORT][REDELIVERY_LOCKED] "
                "job_id=%s task_id=%s countdown=%s",
                job_id,
                task.request.id,
                countdown,
            )
            raise task.retry(
                exc=RuntimeError(
                    "La exportacion redespachada espera que expire "
                    "el bloqueo anterior."
                ),
                countdown=countdown,
            )

        logger.warning(
            "[TASK PUBLICATION_EXPORT][DUPLICATE_SKIPPED] "
            "job_id=%s task_id=%s",
            job_id,
            task.request.id,
        )
        return

    logger.info(
        "[TASK PUBLICATION_EXPORT][START] "
        "job_id=%s user_id=%s creation_date=%s retry=%s",
        job_id,
        user_id,
        creation_date,
        task.request.retries,
    )

    try:
        asyncio.run(
            _export_publications_task(
                job_id=job_id,
                user_id=user_id,
                creation_date=creation_date,
                lock_owner=lock_owner,
            )
        )
        logger.info(
            "[TASK PUBLICATION_EXPORT][OK] job_id=%s",
            job_id,
        )
    except Exception as exc:
        retry_number = int(task.request.retries or 0) + 1
        can_retry = (
            _is_retryable_export_error(exc)
            and retry_number
            <= int(settings.ml_publication_export_task_max_retries)
        )

        if can_retry:
            countdown = _retry_delay_seconds(retry_number)
            logger.exception(
                "[TASK PUBLICATION_EXPORT][RETRY] "
                "job_id=%s retry=%s countdown=%s",
                job_id,
                retry_number,
                countdown,
            )
            JobStore.update(
                job_id,
                status="retrying",
                retry_count=retry_number,
                next_retry_at=time.time() + countdown,
                heartbeat_at=time.time(),
                message=(
                    "Mercado Libre o la red no respondieron correctamente. "
                    f"Reintento {retry_number}/"
                    f"{settings.ml_publication_export_task_max_retries} "
                    f"en {countdown} segundos; el avance fue conservado."
                ),
                last_error=str(exc),
            )
            raise task.retry(exc=exc, countdown=countdown)

        logger.exception(
            "[TASK PUBLICATION_EXPORT][ERROR] job_id=%s",
            job_id,
        )
        JobStore.update(
            job_id,
            status="error",
            message=(
                "No se pudo generar el Excel despues de aplicar los "
                "reintentos de contingencia."
            ),
            last_error=str(exc),
            heartbeat_at=time.time(),
        )
        if requested_by_user_id:
            publication_export_store.release_reference(
                requested_by_user_id=requested_by_user_id,
                seller_id=user_id,
                creation_date=creation_date,
                job_id=job_id,
            )
        raise
    finally:
        publication_export_store.release_run_lock(
            job_id=job_id,
            owner=lock_owner,
        )


async def _export_publications_task(
    *,
    job_id: str,
    user_id: str,
    creation_date: str,
    lock_owner: str,
) -> None:
    lock_lost = asyncio.Event()

    def ensure_lock_and_heartbeat() -> None:
        if lock_lost.is_set() or not publication_export_store.renew_run_lock(
            job_id=job_id,
            owner=lock_owner,
        ):
            lock_lost.set()
            raise RuntimeError(
                "Se perdio el bloqueo de ejecucion de la exportacion."
            )
        JobStore.update(job_id, heartbeat_at=time.time())

    async def renew_lock_periodically() -> None:
        interval = int(
            settings.ml_publication_export_lock_heartbeat_seconds
        )
        while True:
            await asyncio.sleep(interval)
            if not publication_export_store.renew_run_lock(
                job_id=job_id,
                owner=lock_owner,
            ):
                lock_lost.set()
                return
            JobStore.update(job_id, heartbeat_at=time.time())

    current_job = JobStore.get(job_id) or {}
    JobStore.update(
        job_id,
        status="processing",
        progress=max(1, int(current_job.get("progress") or 0)),
        heartbeat_at=time.time(),
        next_retry_at=None,
        message="Preparando exportacion de publicaciones...",
    )

    heartbeat_task = asyncio.create_task(renew_lock_periodically())
    try:
        await ml_client.startup()
        await publication_export_service.export(
            job_id=job_id,
            user_id=user_id,
            creation_date=creation_date,
            heartbeat=ensure_lock_and_heartbeat,
        )
    finally:
        if ml_client.client is not None:
            await ml_client.shutdown()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
