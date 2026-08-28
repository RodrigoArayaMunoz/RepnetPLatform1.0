import asyncio
from datetime import datetime

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.meli_sales_sync_service import MeliSalesSyncService
from services.ml_client import ml_client
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.supabase_meli_sales_store import supabase_meli_sales_store
from services.token_store import token_store

logger = get_task_logger(__name__)


async def _prepare_ml_user(user_id: str) -> None:
    await supabase_meli_connection_store.restore_token_store()
    token = token_store.get(user_id)
    if not token:
        raise RuntimeError(
            "La notificacion corresponde a una cuenta de Mercado Libre no conectada."
        )

    rows = await supabase_meli_connection_store.list_rows(include_tokens=False)
    is_active_user = any(
        row.get("is_active") and str(row.get("ml_user_id") or "") == user_id
        for row in rows
    )
    if not is_active_user:
        raise RuntimeError(
            "La notificacion no pertenece a la cuenta activa de Mercado Libre."
        )


async def _process_notification(payload: dict, event_key: str) -> None:
    await supabase_meli_sales_store.register_notification(
        event_key=event_key,
        payload=payload,
    )
    if await supabase_meli_sales_store.notification_status(event_key) == "processed":
        logger.info("[MELI_WEBHOOK][DUPLICATE] event_key=%s", event_key)
        return

    await supabase_meli_sales_store.mark_notification(
        event_key,
        status="processing",
    )
    user_id = str(payload.get("user_id") or "")
    await _prepare_ml_user(user_id)

    service = MeliSalesSyncService()
    service.start_request_context()
    try:
        await ml_client.startup()
        try:
            await service.process_notification(payload)
        finally:
            await ml_client.shutdown()
    finally:
        await service.close_request_context()

    await supabase_meli_sales_store.mark_notification(
        event_key,
        status="processed",
    )


@celery_app.task(
    bind=True,
    name="tasks.process_meli_notification",
    max_retries=8,
    acks_late=True,
)
def process_meli_notification_task(task, payload: dict, event_key: str) -> None:
    try:
        asyncio.run(_process_notification(payload, event_key))
    except Exception as exc:
        logger.exception("[MELI_WEBHOOK][ERROR] event_key=%s", event_key)
        try:
            asyncio.run(
                supabase_meli_sales_store.mark_notification(
                    event_key,
                    status="failed",
                    error=str(exc)[:2000],
                )
            )
        except Exception:
            logger.exception(
                "[MELI_WEBHOOK][MARK_FAILED_ERROR] event_key=%s",
                event_key,
            )
        countdown = min(2 ** max(task.request.retries, 0), 300)
        raise task.retry(exc=exc, countdown=countdown)


async def _backfill_sales(
    *,
    user_id: str,
    range_start: str,
    range_end: str,
) -> dict[str, int]:
    await _prepare_ml_user(user_id)
    service = MeliSalesSyncService()
    service.start_request_context()
    try:
        await ml_client.startup()
        try:
            processed = await service.backfill_paid_orders(
                user_id=user_id,
                range_start=datetime.fromisoformat(range_start),
                range_end=datetime.fromisoformat(range_end),
            )
        finally:
            await ml_client.shutdown()
    finally:
        await service.close_request_context()
    return {"processed": processed}


@celery_app.task(
    bind=True,
    name="tasks.backfill_meli_sales",
    max_retries=5,
    acks_late=True,
)
def backfill_meli_sales_task(
    task,
    user_id: str,
    range_start: str,
    range_end: str,
) -> dict[str, int]:
    try:
        return asyncio.run(
            _backfill_sales(
                user_id=user_id,
                range_start=range_start,
                range_end=range_end,
            )
        )
    except Exception as exc:
        logger.exception("[MELI_SALES_BACKFILL][ERROR] user_id=%s", user_id)
        countdown = min(5 * (2 ** max(task.request.retries, 0)), 300)
        raise task.retry(exc=exc, countdown=countdown)
