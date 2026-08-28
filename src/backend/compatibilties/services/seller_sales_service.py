import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from config import settings
from services.ml_client import ml_client
from services.redis_rate_limiter import RedisWindowRateLimiter
from services.supabase_meli_sales_store import supabase_meli_sales_store

logger = logging.getLogger(__name__)


class SellerSalesService:
    MAX_PAGES = 200

    def __init__(self) -> None:
        self._read_rate_limiter: RedisWindowRateLimiter | None = None

    def start_request_context(self) -> None:
        self._read_rate_limiter = RedisWindowRateLimiter(
            redis_url=settings.redis_url,
            namespace="ml:read",
            requests_per_second=float(settings.ml_read_requests_per_second),
        )

    async def close_request_context(self) -> None:
        if self._read_rate_limiter is None:
            return

        await self._read_rate_limiter.client.aclose()
        self._read_rate_limiter = None

    async def _request_ml(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._read_rate_limiter is None:
            self.start_request_context()

        return await ml_client.request(
            method,
            path,
            params=params,
            user_id=user_id,
            rate_limiter=self._read_rate_limiter,
        )

    @staticmethod
    def _format_ml_datetime(value: datetime) -> str:
        return value.isoformat(timespec="milliseconds")

    @staticmethod
    def _parse_ml_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None

        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _extract_note_texts(payload: Any) -> list[str]:
        notes: list[str] = []
        seen: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for entry in value:
                    visit(entry)
                return
            if not isinstance(value, dict):
                return

            note = str(value.get("note") or "").strip()
            if note and note not in seen:
                notes.append(note)
                seen.add(note)

            for key in ("results", "notes", "values"):
                nested = value.get(key)
                if isinstance(nested, (list, dict)):
                    visit(nested)

        visit(payload)
        return notes

    async def _resolve_seller(self, user_id: str) -> dict[str, str]:
        payload = await self._request_ml("GET", "/users/me", user_id=user_id)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise HTTPException(
                status_code=502,
                detail="Mercado Libre no devolvio el seller_id de REPNET.",
            )

        return {
            "id": str(payload["id"]),
            "nickname": str(payload.get("nickname") or "REPNET"),
            "site_id": str(payload.get("site_id") or settings.ml_site_id),
        }

    async def _search_paid_orders(
        self,
        *,
        user_id: str,
        seller_id: str,
        range_start: datetime,
        range_end: datetime,
    ) -> list[dict[str, Any]]:
        page_limit = int(settings.seller_sales_page_limit)
        offset = 0
        orders: list[dict[str, Any]] = []

        for _page_number in range(self.MAX_PAGES):
            payload = await self._request_ml(
                "GET",
                "/orders/search",
                user_id=user_id,
                params={
                    "seller": seller_id,
                    "order.status": "paid",
                    "order.date_closed.from": self._format_ml_datetime(range_start),
                    "order.date_closed.to": self._format_ml_datetime(range_end),
                    "sort": "date_desc",
                    "limit": page_limit,
                    "offset": offset,
                },
            )
            if not isinstance(payload, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Mercado Libre devolvio una busqueda de ventas invalida.",
                )

            results = payload.get("results")
            if not isinstance(results, list):
                raise HTTPException(
                    status_code=502,
                    detail="Mercado Libre no devolvio la lista de ventas esperada.",
                )

            orders.extend(order for order in results if isinstance(order, dict))

            paging = payload.get("paging") or {}
            total = int(paging.get("total") or len(orders))
            returned_limit = int(paging.get("limit") or page_limit)
            if not results or offset + returned_limit >= total:
                break
            offset += returned_limit
        else:
            raise HTTPException(
                status_code=502,
                detail="La consulta de ventas excedio el limite seguro de paginacion.",
            )

        return orders

    async def _load_order_notes(
        self,
        *,
        order_id: str,
        user_id: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[list[str], bool]:
        try:
            async with semaphore:
                payload = await self._request_ml(
                    "GET",
                    f"/orders/{order_id}/notes",
                    user_id=user_id,
                )
            return self._extract_note_texts(payload), False
        except HTTPException as exc:
            if exc.status_code == 404:
                return [], False
            logger.warning(
                "No se pudieron consultar las notas de la orden %s: %s",
                order_id,
                exc.detail,
            )
            return [], True
        except Exception as exc:
            logger.warning(
                "Error inesperado consultando notas de la orden %s: %s",
                order_id,
                exc,
            )
            return [], True

    async def get_daily_sales(
        self,
        *,
        user_id: str,
        sales_date: date | None = None,
        sales_date_to: date | None = None,
    ) -> dict[str, Any]:
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

        range_start = datetime.combine(requested_date, time.min, tzinfo=timezone)
        range_end = datetime.combine(
            requested_date_to + timedelta(days=1),
            time.min,
            tzinfo=timezone,
        )

        seller = await self._resolve_seller(user_id)
        raw_orders = await self._search_paid_orders(
            user_id=user_id,
            seller_id=seller["id"],
            range_start=range_start,
            range_end=range_end,
        )

        filtered_orders: list[dict[str, Any]] = []
        seen_order_ids: set[str] = set()
        for order in raw_orders:
            order_id = str(order.get("id") or "").strip()
            closed_at = self._parse_ml_datetime(order.get("date_closed"))
            if not order_id or not closed_at or closed_at.tzinfo is None:
                continue
            closed_at = closed_at.astimezone(timezone)
            if not range_start <= closed_at < range_end:
                continue
            if str(order.get("status") or "").lower() != "paid":
                continue
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
            filtered_orders.append(order)

        semaphore = asyncio.Semaphore(
            int(settings.seller_sales_note_concurrency)
        )
        note_results = await asyncio.gather(
            *(
                self._load_order_notes(
                    order_id=str(order["id"]),
                    user_id=user_id,
                    semaphore=semaphore,
                )
                for order in filtered_orders
            )
        )

        sales: list[dict[str, Any]] = []
        notes_failed = 0
        for order, (notes, note_error) in zip(filtered_orders, note_results):
            order_id = str(order["id"])
            notes_failed += int(note_error)
            sales.append(
                {
                    "mlc": order_id,
                    "sale_note": " | ".join(notes),
                    "notes": notes,
                    "notes_error": note_error,
                    "order_id": order_id,
                    "pack_id": (
                        str(order.get("pack_id"))
                        if order.get("pack_id") is not None
                        else None
                    ),
                    "status": str(order.get("status") or ""),
                    "date_closed": str(order.get("date_closed") or ""),
                }
            )

        return {
            "account": "repnet",
            "seller": seller,
            "date": requested_date.isoformat(),
            "date_from": requested_date.isoformat(),
            "date_to": requested_date_to.isoformat(),
            "timezone": settings.seller_sales_timezone,
            "range": {
                "from": self._format_ml_datetime(range_start),
                "to_exclusive": self._format_ml_datetime(range_end),
            },
            "total": len(sales),
            "notes_failed": notes_failed,
            "sales": sales,
        }

    async def get_stored_daily_sales(
        self,
        *,
        user_id: str,
        sales_date: date | None = None,
        sales_date_to: date | None = None,
    ) -> dict[str, Any]:
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

        range_start = datetime.combine(requested_date, time.min, tzinfo=timezone)
        range_end = datetime.combine(
            requested_date_to + timedelta(days=1),
            time.min,
            tzinfo=timezone,
        )
        await supabase_meli_sales_store.ensure_ready()
        sales = await supabase_meli_sales_store.list_sales(
            seller_id=user_id,
            range_start=range_start,
            range_end=range_end,
        )
        return {
            "account": "repnet",
            "seller": {"id": user_id, "nickname": "REPNET", "site_id": settings.ml_site_id},
            "date": requested_date.isoformat(),
            "date_from": requested_date.isoformat(),
            "date_to": requested_date_to.isoformat(),
            "timezone": settings.seller_sales_timezone,
            "range": {
                "from": self._format_ml_datetime(range_start),
                "to_exclusive": self._format_ml_datetime(range_end),
            },
            "source": "supabase",
            "total": len(sales),
            "sales": sales,
        }
