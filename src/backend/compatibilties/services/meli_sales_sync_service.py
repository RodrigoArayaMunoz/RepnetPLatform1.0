import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from config import settings
from services.ml_client import ml_client
from services.redis_rate_limiter import RedisWindowRateLimiter
from services.supabase_meli_sales_store import supabase_meli_sales_store

logger = logging.getLogger(__name__)


class MeliSalesSyncService:
    MAX_PAGES = 200

    def __init__(self) -> None:
        self._read_rate_limiter: RedisWindowRateLimiter | None = None

    def start_request_context(self) -> None:
        self._read_rate_limiter = RedisWindowRateLimiter(
            redis_url=settings.redis_url,
            namespace="ml:webhook-read",
            requests_per_second=float(settings.ml_read_requests_per_second),
        )

    async def close_request_context(self) -> None:
        if self._read_rate_limiter is not None:
            await self._read_rate_limiter.client.aclose()
            self._read_rate_limiter = None

    async def _request_ml(
        self,
        path: str,
        *,
        user_id: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._read_rate_limiter is None:
            self.start_request_context()
        return await ml_client.request(
            "GET",
            path,
            params=params,
            user_id=user_id,
            rate_limiter=self._read_rate_limiter,
        )

    @staticmethod
    def _id(value: Any) -> str | None:
        text = str(value or "").strip()
        return text if text and text.isdigit() else None

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def _sku_from_item(cls, item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        seller_sku = str(item.get("seller_sku") or "").strip() or None
        custom_field = str(item.get("seller_custom_field") or "").strip() or None

        attribute_sku: str | None = None
        attributes = item.get("variation_attributes")
        if isinstance(attributes, list):
            for attribute in attributes:
                if not isinstance(attribute, dict):
                    continue
                attribute_id = str(
                    attribute.get("id") or attribute.get("name") or ""
                ).strip().upper()
                if attribute_id != "SELLER_SKU":
                    continue
                attribute_sku = str(
                    attribute.get("value_name") or attribute.get("value") or ""
                ).strip() or None
                if attribute_sku:
                    break

        return seller_sku, custom_field, seller_sku or custom_field or attribute_sku

    @staticmethod
    def _extract_notes(payload: Any) -> list[str]:
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
                seen.add(note)
                notes.append(note)
            for key in ("results", "notes", "values"):
                nested = value.get(key)
                if isinstance(nested, (list, dict)):
                    visit(nested)

        visit(payload)
        return notes

    async def _load_notes(self, order_id: str, user_id: str) -> tuple[list[str], bool]:
        try:
            payload = await self._request_ml(
                f"/orders/{order_id}/notes",
                user_id=user_id,
            )
            return self._extract_notes(payload), False
        except HTTPException as exc:
            if exc.status_code == 404:
                return [], False
            logger.warning(
                "[MELI_SALES_SYNC][NOTES_ERROR] order_id=%s detail=%s",
                order_id,
                exc.detail,
            )
            return [], True

    def _normalize_order(
        self,
        order: dict[str, Any],
        *,
        user_id: str,
        pack_shipment_id: str | None,
        notes: list[str],
        notes_error: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        order_id = self._id(order.get("id"))
        if not order_id:
            raise ValueError("Mercado Libre devolvio una orden sin ID valido.")

        seller = order.get("seller") if isinstance(order.get("seller"), dict) else {}
        seller_id = self._id(seller.get("id")) or user_id
        if seller_id != user_id:
            raise ValueError("La orden notificada no pertenece al vendedor conectado.")

        pack_id = self._id(order.get("pack_id"))
        shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
        shipment_id = self._id(shipping.get("id")) or pack_shipment_id
        synced_at = self._now()
        order_row = {
            "order_id": order_id,
            "seller_id": seller_id,
            "sale_id": pack_id or order_id,
            "pack_id": pack_id,
            "shipment_id": shipment_id,
            "status": str(order.get("status") or ""),
            "status_detail": str(order.get("status_detail") or "") or None,
            "date_created": order.get("date_created") or None,
            "date_closed": order.get("date_closed") or None,
            "last_updated": (
                order.get("date_last_updated") or order.get("last_updated") or None
            ),
            "total_amount": order.get("total_amount"),
            "paid_amount": order.get("paid_amount"),
            "currency_id": str(order.get("currency_id") or "") or None,
            "buyer_id": self._id(
                (order.get("buyer") or {}).get("id")
                if isinstance(order.get("buyer"), dict)
                else None
            ),
            "sale_note": " | ".join(notes),
            "notes": notes,
            "notes_error": notes_error,
            "raw_data": order,
            "synced_at": synced_at,
        }

        item_rows: list[dict[str, Any]] = []
        order_items = order.get("order_items")
        if not isinstance(order_items, list):
            order_items = []
        for line_number, order_item in enumerate(order_items):
            if not isinstance(order_item, dict):
                continue
            item = order_item.get("item")
            if not isinstance(item, dict):
                item = {}
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            seller_sku, custom_field, resolved_sku = self._sku_from_item(item)
            item_rows.append(
                {
                    "order_id": order_id,
                    "line_number": line_number,
                    "seller_id": seller_id,
                    "item_id": item_id,
                    "user_product_id": str(item.get("user_product_id") or "").strip() or None,
                    "variation_id": str(item.get("variation_id") or "").strip() or None,
                    "seller_sku": seller_sku,
                    "seller_custom_field": custom_field,
                    "sku": resolved_sku,
                    "title": str(item.get("title") or ""),
                    "quantity": int(order_item.get("quantity") or 0),
                    "unit_price": order_item.get("unit_price"),
                    "full_unit_price": order_item.get("full_unit_price"),
                    "currency_id": str(order_item.get("currency_id") or "") or None,
                    "raw_data": order_item,
                    "synced_at": synced_at,
                }
            )
        return order_row, item_rows

    def _normalize_shipment(
        self,
        shipment: dict[str, Any],
        *,
        user_id: str,
    ) -> dict[str, Any]:
        shipment_id = self._id(shipment.get("id"))
        if not shipment_id:
            raise ValueError("Mercado Libre devolvio un envio sin ID valido.")

        sender_id = self._id(shipment.get("sender_id")) or user_id
        if sender_id != user_id:
            raise ValueError("El envio notificado no pertenece al vendedor conectado.")
        logistic_type = str(shipment.get("logistic_type") or "").strip() or None
        return {
            "shipment_id": shipment_id,
            "seller_id": sender_id,
            "pack_id": self._id(shipment.get("pack_id")),
            "status": str(shipment.get("status") or ""),
            "substatus": str(shipment.get("substatus") or "") or None,
            "shipment_type": str(shipment.get("type") or "") or None,
            "mode": str(shipment.get("mode") or "") or None,
            "logistic_type": logistic_type,
            "shipping_type": "flex" if logistic_type == "self_service" else "normal",
            "tracking_number": str(shipment.get("tracking_number") or "") or None,
            "tracking_method": str(shipment.get("tracking_method") or "") or None,
            "date_created": shipment.get("date_created") or None,
            "last_updated": shipment.get("last_updated") or None,
            "raw_data": shipment,
            "synced_at": self._now(),
        }

    async def _load_pack(self, pack_id: str, user_id: str) -> dict[str, Any] | None:
        try:
            payload = await self._request_ml(f"/packs/{pack_id}", user_id=user_id)
        except HTTPException as exc:
            if exc.status_code in (403, 404):
                logger.warning(
                    "[MELI_SALES_SYNC][PACK_UNAVAILABLE] pack_id=%s status=%s",
                    pack_id,
                    exc.status_code,
                )
                return None
            raise
        return payload if isinstance(payload, dict) else None

    async def hydrate_order(
        self,
        *,
        order_id: str,
        user_id: str,
        shipment_payload: dict[str, Any] | None = None,
    ) -> set[str]:
        initial = await self._request_ml(f"/orders/{order_id}", user_id=user_id)
        if not isinstance(initial, dict):
            raise ValueError("Mercado Libre devolvio una orden invalida.")

        pack_id = self._id(initial.get("pack_id"))
        pack_payload = await self._load_pack(pack_id, user_id) if pack_id else None
        pack_shipment_id = self._id(
            (pack_payload.get("shipment") or {}).get("id")
            if isinstance(pack_payload, dict)
            and isinstance(pack_payload.get("shipment"), dict)
            else None
        )
        if pack_id and pack_payload:
            await supabase_meli_sales_store.upsert_pack(
                {
                    "pack_id": pack_id,
                    "seller_id": user_id,
                    "shipment_id": pack_shipment_id,
                    "status": str(pack_payload.get("status") or "") or None,
                    "status_detail": str(pack_payload.get("status_detail") or "") or None,
                    "buyer_id": self._id(
                        (pack_payload.get("buyer") or {}).get("id")
                        if isinstance(pack_payload.get("buyer"), dict)
                        else None
                    ),
                    "date_created": pack_payload.get("date_created") or None,
                    "last_updated": pack_payload.get("last_updated") or None,
                    "raw_data": pack_payload,
                    "synced_at": self._now(),
                }
            )

        orders_to_save: list[dict[str, Any]] = [initial]
        pack_orders = pack_payload.get("orders") if isinstance(pack_payload, dict) else None
        if isinstance(pack_orders, list):
            for pack_order in pack_orders:
                candidate_id = self._id(
                    pack_order.get("id") if isinstance(pack_order, dict) else pack_order
                )
                if not candidate_id or candidate_id == order_id:
                    continue
                try:
                    candidate = await self._request_ml(
                        f"/orders/{candidate_id}",
                        user_id=user_id,
                    )
                except HTTPException as exc:
                    if exc.status_code in (403, 404):
                        continue
                    raise
                if isinstance(candidate, dict):
                    candidate_seller = candidate.get("seller")
                    candidate_seller_id = self._id(
                        candidate_seller.get("id")
                        if isinstance(candidate_seller, dict)
                        else None
                    )
                    if not candidate_seller_id or candidate_seller_id == user_id:
                        orders_to_save.append(candidate)

        saved_order_ids: set[str] = set()
        resolved_shipment_id = pack_shipment_id
        for order in orders_to_save:
            current_order_id = self._id(order.get("id"))
            if not current_order_id or current_order_id in saved_order_ids:
                continue
            notes, notes_error = await self._load_notes(current_order_id, user_id)
            order_row, item_rows = self._normalize_order(
                order,
                user_id=user_id,
                pack_shipment_id=pack_shipment_id,
                notes=notes,
                notes_error=notes_error,
            )
            await supabase_meli_sales_store.upsert_order(order_row, item_rows)
            saved_order_ids.add(current_order_id)
            resolved_shipment_id = resolved_shipment_id or order_row.get("shipment_id")

        if shipment_payload is None and resolved_shipment_id:
            try:
                loaded_shipment = await self._request_ml(
                    f"/shipments/{resolved_shipment_id}",
                    user_id=user_id,
                )
                shipment_payload = loaded_shipment if isinstance(loaded_shipment, dict) else None
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise

        if shipment_payload:
            shipment_row = self._normalize_shipment(shipment_payload, user_id=user_id)
            await supabase_meli_sales_store.upsert_shipment(shipment_row)
            shipment_id = str(shipment_row["shipment_id"])
            relation_type = str(shipment_payload.get("type") or "forward")
            for saved_order_id in saved_order_ids:
                await supabase_meli_sales_store.link_order_shipment(
                    order_id=saved_order_id,
                    shipment_id=shipment_id,
                    relation_type=relation_type,
                )

        return saved_order_ids

    async def hydrate_shipment(self, *, shipment_id: str, user_id: str) -> None:
        payload = await self._request_ml(f"/shipments/{shipment_id}", user_id=user_id)
        if not isinstance(payload, dict):
            raise ValueError("Mercado Libre devolvio un envio invalido.")

        shipment_row = self._normalize_shipment(payload, user_id=user_id)
        await supabase_meli_sales_store.upsert_shipment(shipment_row)
        order_id = self._id(payload.get("order_id"))
        if order_id:
            await self.hydrate_order(
                order_id=order_id,
                user_id=user_id,
                shipment_payload=payload,
            )

    async def process_notification(self, payload: dict[str, Any]) -> None:
        topic = str(payload.get("topic") or "")
        resource = str(payload.get("resource") or "")
        user_id = str(payload.get("user_id") or "")
        resource_id = resource.rstrip("/").rsplit("/", 1)[-1]
        if topic == "orders_v2":
            await self.hydrate_order(order_id=resource_id, user_id=user_id)
            return
        if topic == "shipments":
            await self.hydrate_shipment(shipment_id=resource_id, user_id=user_id)
            return
        raise ValueError(f"Topico de Mercado Libre no soportado: {topic}")

    async def backfill_paid_orders(
        self,
        *,
        user_id: str,
        range_start: datetime,
        range_end: datetime,
    ) -> int:
        page_limit = int(settings.seller_sales_page_limit)
        offset = 0
        processed: set[str] = set()
        for _ in range(self.MAX_PAGES):
            payload = await self._request_ml(
                "/orders/search",
                user_id=user_id,
                params={
                    "seller": user_id,
                    "order.status": "paid",
                    "order.date_closed.from": range_start.isoformat(timespec="milliseconds"),
                    "order.date_closed.to": range_end.isoformat(timespec="milliseconds"),
                    "sort": "date_desc",
                    "limit": page_limit,
                    "offset": offset,
                },
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Mercado Libre devolvio una busqueda de ventas invalida.")
            results = payload["results"]
            for order in results:
                candidate_id = self._id(order.get("id") if isinstance(order, dict) else None)
                if not candidate_id or candidate_id in processed:
                    continue
                processed.update(
                    await self.hydrate_order(order_id=candidate_id, user_id=user_id)
                )

            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            total = int(paging.get("total") or len(processed))
            returned_limit = int(paging.get("limit") or page_limit)
            if not results or offset + returned_limit >= total:
                return len(processed)
            offset += returned_limit
        raise RuntimeError("La sincronizacion excedio el limite seguro de paginacion.")


meli_sales_sync_service = MeliSalesSyncService()
