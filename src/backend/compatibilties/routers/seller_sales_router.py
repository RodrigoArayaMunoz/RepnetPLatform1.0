from datetime import date

from fastapi import APIRouter, HTTPException, Query

from services.seller_sales_service import SellerSalesService
from services.supabase_meli_connection_store import supabase_meli_connection_store

router = APIRouter(prefix="/ml/sales", tags=["seller-sales"])


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
        return await seller_sales_service.get_daily_sales(
            user_id=user_id,
            sales_date=sales_date,
            sales_date_to=sales_date_to,
        )
    finally:
        await seller_sales_service.close_request_context()
