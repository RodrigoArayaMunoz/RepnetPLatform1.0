import asyncio

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.item_pictures_service import process_item_pictures_job
from services.job_store import JobStore
from services.ml_client import ml_client

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.process_item_pictures_job")
def process_item_pictures_task(job_id: str, user_id: str, file_path: str) -> None:
    logger.info(
        "[TASK ITEM_PICTURES][START] job_id=%s user_id=%s file_path=%s",
        job_id,
        user_id,
        file_path,
    )
    asyncio.run(_process_item_pictures_task(job_id, user_id, file_path))


async def _process_item_pictures_task(job_id: str, user_id: str, file_path: str) -> None:
    try:
        JobStore.update(
            job_id,
            status="processing",
            progress=1,
            message="Inicializando actualización de fotos...",
        )

        await ml_client.startup()
        try:
            await process_item_pictures_job(
                job_id=job_id,
                user_id=user_id,
                file_path=file_path,
            )
        finally:
            await ml_client.shutdown()

        logger.info("[TASK ITEM_PICTURES][OK] job_id=%s", job_id)
    except Exception as exc:
        logger.exception("[TASK ITEM_PICTURES][ERROR] job_id=%s", job_id)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error actualizando fotos: {str(exc)}",
        )
        raise
