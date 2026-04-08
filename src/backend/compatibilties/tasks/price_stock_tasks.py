import asyncio

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.job_store import JobStore
from services.ml_client import ml_client
from services.price_stock_service import process_price_stock_job

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.process_price_stock_job")
def process_price_stock_task(job_id: str, user_id: str, file_path: str) -> None:
    logger.info(
        "[TASK PRICE_STOCK][START] job_id=%s user_id=%s file_path=%s",
        job_id,
        user_id,
        file_path,
    )
    asyncio.run(_process_price_stock_task(job_id, user_id, file_path))


async def _process_price_stock_task(job_id: str, user_id: str, file_path: str) -> None:
    try:
        JobStore.update(
            job_id,
            status="processing",
            progress=1,
            message="Inicializando actualización de precios y stock...",
        )

        await ml_client.startup()
        try:
            await process_price_stock_job(
                job_id=job_id,
                user_id=user_id,
                file_path=file_path,
            )
        finally:
            await ml_client.shutdown()

        logger.info("[TASK PRICE_STOCK][OK] job_id=%s", job_id)
    except Exception as exc:
        logger.exception("[TASK PRICE_STOCK][ERROR] job_id=%s", job_id)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error actualizando precios y stock: {str(exc)}",
        )
        raise
