from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from celery_app import celery_app
from config import settings
from services.seller_sales_service import SellerSalesService
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.supabase_meli_sales_store import supabase_meli_sales_store
from tasks.meli_sales_tasks import backfill_meli_sales_task

router = APIRouter(prefix="/ml/sales", tags=["seller-sales"])


class PickingStatusUpdate(BaseModel):
    status: Literal["in_preparation", "packed"]
    scanned_sku: str | None = None


async def _get_connected_ml_user_id() -> str:
    rows = await supabase_meli_connection_store.list_rows(include_tokens=False)
    if not rows:
        raise HTTPException(
            status_code=401,
            detail="No hay una cuenta de Mercado Libre conectada.",
        )

    row = rows[0]
    user_id = row.get("ml_user_id")
    if not row.get("is_active") or not user_id:
        raise HTTPException(
            status_code=401,
            detail="La cuenta de Mercado Libre no esta conectada.",
        )
    return str(user_id)


@router.get("/today")
async def get_repnet_daily_sales(
    sales_date: date | None = Query(default=None, alias="date"),
    sales_date_to: date | None = Query(default=None, alias="date_to"),
):
    user_id = await _get_connected_ml_user_id()
    seller_sales_service = SellerSalesService()
    seller_sales_service.start_request_context()
    try:
        return await seller_sales_service.get_stored_daily_sales(
            user_id=user_id,
            sales_date=sales_date,
            sales_date_to=sales_date_to,
        )
    finally:
        await seller_sales_service.close_request_context()


@router.put("/{sale_id}/picking-status")
async def update_sale_picking_status(
    sale_id: str,
    payload: PickingStatusUpdate,
):
    if not sale_id.isdigit():
        raise HTTPException(status_code=422, detail="El ID de venta no es valido.")

    user_id = await _get_connected_ml_user_id()
    try:
        picking = await supabase_meli_sales_store.set_sale_picking_status(
            seller_id=user_id,
            sale_id=sale_id,
            status=payload.status,
            scanned_sku=payload.scanned_sku,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="No fue posible guardar el estado de picking.",
        ) from exc

    return {"ok": True, **picking}


def _sales_range(
    sales_date: date | None,
    sales_date_to: date | None,
) -> tuple[date, date, datetime, datetime]:
    timezone = ZoneInfo(settings.seller_sales_timezone)
    requested_date = sales_date or datetime.now(timezone).date()
    requested_date_to = sales_date_to or requested_date
    if requested_date_to < requested_date:
        raise HTTPException(
            status_code=422,
            detail="La fecha final no puede ser anterior a la fecha inicial.",
        )
    if (requested_date_to - requested_date).days > 31:
        raise HTTPException(
            status_code=422,
            detail="El rango de ventas no puede superar 32 dias.",
        )
    return (
        requested_date,
        requested_date_to,
        datetime.combine(requested_date, time.min, tzinfo=timezone),
        datetime.combine(
            requested_date_to + timedelta(days=1),
            time.min,
            tzinfo=timezone,
        ),
    )


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_repnet_sales(
    sales_date: date | None = Query(default=None, alias="date"),
    sales_date_to: date | None = Query(default=None, alias="date_to"),
):
    user_id = await _get_connected_ml_user_id()
    requested_date, requested_date_to, range_start, range_end = _sales_range(
        sales_date,
        sales_date_to,
    )
    task = backfill_meli_sales_task.apply_async(
        args=[user_id, range_start.isoformat(), range_end.isoformat()],
    )
    return {
        "ok": True,
        "task_id": task.id,
        "status": "queued",
        "date_from": requested_date.isoformat(),
        "date_to": requested_date_to.isoformat(),
    }


@router.get("/sync/{task_id}")
async def get_sales_sync_status(task_id: str):
    task = AsyncResult(task_id, app=celery_app)
    response = {
        "task_id": task_id,
        "status": task.status.lower(),
        "ready": task.ready(),
        "successful": task.successful() if task.ready() else False,
    }
    if task.successful():
        response["result"] = task.result
    elif task.failed():
        response["error"] = str(task.result or "La sincronizacion fallo.")[:500]
    return response
