import asyncio

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.process_queue_service import run_process_queue
from services.process_queue_store import process_queue_store

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.run_process_queue_job")
def run_process_queue_task(user_id: str) -> None:
    logger.info("[TASK PROCESS_QUEUE][START] user_id=%s", user_id)
    asyncio.run(_run_process_queue_task(user_id))


async def _run_process_queue_task(user_id: str) -> None:
    try:
        await run_process_queue(user_id=user_id)
        logger.info("[TASK PROCESS_QUEUE][OK] user_id=%s", user_id)
    except Exception as exc:
        logger.exception("[TASK PROCESS_QUEUE][ERROR] user_id=%s", user_id)
        process_queue_store.finish(
            message=f"Cola detenida por error: {str(exc)}",
            last_error=str(exc),
        )
        raise
