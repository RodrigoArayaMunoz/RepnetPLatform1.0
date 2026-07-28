import asyncio

from celery.utils.log import get_task_logger
from fastapi import HTTPException

from celery_app import celery_app
from services.ml_client import ml_client
from services.publication_sync_service import publication_sync_service
from services.publication_sync_store import publication_sync_store

logger = get_task_logger(__name__)


def _sync_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            if (
                exc.status_code == 403
                and detail.get("retry_reason") == "unknown_forbidden"
            ):
                return (
                    "Mercado Libre rechazó temporalmente un lote de "
                    "publicaciones después de varios reintentos (403). "
                    "Las publicaciones ya guardadas en Supabase se conservaron."
                )

            detail_message = detail.get("message")
            if detail_message:
                return str(detail_message)

        if isinstance(detail, str) and detail.strip():
            return detail.strip()

    message = str(exc).strip()
    return message or "Error inesperado durante la carga de publicaciones."


@celery_app.task(bind=True, name="tasks.sync_publications_job")
def sync_publications_task(task, user_id: str) -> None:
    delivery_info = getattr(task.request, "delivery_info", {}) or {}
    state = publication_sync_store.get_state()
    is_stale_redelivery = bool(delivery_info.get("redelivered")) and (
        not state.get("running")
        or state.get("task_id") != task.request.id
    )
    if is_stale_redelivery:
        logger.warning(
            "[TASK PUBLICATION_SYNC][SKIP_REDELIVERED] task_id=%s "
            "state_task_id=%s running=%s",
            task.request.id,
            state.get("task_id"),
            state.get("running"),
        )
        return

    logger.info("[TASK PUBLICATION_SYNC][START] user_id=%s", user_id)
    asyncio.run(_sync_publications_task(user_id))


async def _sync_publications_task(user_id: str) -> None:
    try:
        publication_sync_service.start_request_context()
        try:
            await ml_client.startup()
            try:
                await publication_sync_service.sync_publications(user_id=user_id)
            finally:
                await ml_client.shutdown()
        finally:
            await publication_sync_service.close_request_context()

        logger.info("[TASK PUBLICATION_SYNC][OK] user_id=%s", user_id)
    except Exception as exc:
        logger.exception("[TASK PUBLICATION_SYNC][ERROR] user_id=%s", user_id)
        error_message = _sync_error_message(exc)
        publication_sync_store.finish(
            status="error",
            message="La carga de publicaciones terminó con error.",
            last_error=error_message,
        )
        raise RuntimeError(error_message) from exc
