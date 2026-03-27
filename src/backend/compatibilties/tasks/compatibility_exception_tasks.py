import asyncio

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.job_store import JobStore
from services.ml_client import ml_client
from services.compatibility_exception_service import process_compatibility_exceptions_job

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.process_compatibility_exceptions_job")
def process_compatibility_exceptions_task(job_id: str, user_id: str, file_path: str) -> None:
    logger.info(
        "[TASK EXCEPTIONS][START] job_id=%s user_id=%s file_path=%s",
        job_id,
        user_id,
        file_path,
    )
    asyncio.run(_process_compatibility_exceptions_task(job_id, user_id, file_path))


async def _process_compatibility_exceptions_task(job_id: str, user_id: str, file_path: str) -> None:
    try:
        JobStore.update(
            job_id,
            status="processing",
            progress=1,
            message="Inicializando procesamiento de excepciones...",
        )

        await ml_client.startup()
        try:
            await process_compatibility_exceptions_job(
                job_id=job_id,
                user_id=user_id,
                file_path=file_path,
            )
        finally:
            await ml_client.shutdown()

        logger.info("[TASK EXCEPTIONS][OK] job_id=%s", job_id)
    except Exception as exc:
        logger.exception("[TASK EXCEPTIONS][ERROR] job_id=%s", job_id)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error informando excepciones de compatibilidad: {str(exc)}",
        )
        raise
