import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

DISPATCHED_SHIPPING_STATUSES = frozenset(
    {"shipped", "delivered", "not_delivered"}
)
DISPATCHED_READY_TO_SHIP_SUBSTATUSES = frozenset(
    {"picked_up", "authorized_by_carrier", "in_hub"}
)
PENDING_DISPATCH_SHIPPING_STATUSES = frozenset(
    {"pending", "handling", "ready_to_ship"}
)


def is_shipment_dispatched(status: Any, substatus: Any = None) -> bool | None:
    normalized_status = str(status or "").strip().lower()
    normalized_substatus = str(substatus or "").strip().lower()
    if normalized_status in DISPATCHED_SHIPPING_STATUSES or (
        normalized_status == "ready_to_ship"
        and normalized_substatus in DISPATCHED_READY_TO_SHIP_SUBSTATUSES
    ):
        return True
    if normalized_status in PENDING_DISPATCH_SHIPPING_STATUSES:
        return False
    return None


class SupabaseMeliSalesStore:
    READ_PAGE_SIZE = 1000

    def __init__(self) -> None:
        self.notifications_table = settings.supabase_meli_notifications_table
        self.packs_table = settings.supabase_meli_packs_table
        self.orders_table = settings.supabase_meli_orders_table
        self.order_items_table = settings.supabase_meli_order_items_table
        self.shipments_table = settings.supabase_meli_shipments_table
        self.order_shipments_table = settings.supabase_meli_order_shipments_table
        self.sale_pickings_table = settings.supabase_meli_sale_pickings_table

    def _table_url(self, table_name: str) -> str:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "Supabase no esta configurado para ventas de Mercado Libre. "
                "Revisa SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY."
            )
        return f"{settings.supabase_url.rstrip('/')}/rest/v1/{table_name}"

    @staticmethod
    def _headers(*, prefer: str | None = None) -> dict[str, str]:
        service_key = settings.supabase_service_role_key
        if not service_key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY no configurado")

        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def _request(
        self,
        method: str,
        table_name: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
        allow_conflict: bool = False,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method,
                self._table_url(table_name),
                headers=self._headers(prefer=prefer),
                params=params,
                json=json_body,
            )

        if response.status_code >= 400 and not (
            allow_conflict and response.status_code == 409
        ):
            logger.error(
                "[SUPABASE_MELI_SALES][%s][%s] status=%s body=%s",
                table_name,
                method,
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                f"Supabase rechazo {method} sobre {table_name} "
                f"({response.status_code})."
            )
        return response

    async def ensure_ready(self) -> None:
        checks = {
            self.notifications_table: "event_key,processing_status",
            self.orders_table: "order_id,seller_id,sale_id",
            self.order_items_table: "order_id,line_number,sku,quantity",
            self.shipments_table: "shipment_id,logistic_type,shipping_type",
        }
        for table_name, select_columns in checks.items():
            await self._request(
                "GET",
                table_name,
                params={"select": select_columns, "limit": "1"},
            )

    async def register_notification(
        self,
        *,
        event_key: str,
        payload: dict[str, Any],
    ) -> bool:
        row = {
            "event_key": event_key,
            "notification_id": str(payload.get("_id") or payload.get("id") or "") or None,
            "topic": str(payload.get("topic") or ""),
            "resource": str(payload.get("resource") or ""),
            "ml_user_id": str(payload.get("user_id") or ""),
            "application_id": str(payload.get("application_id") or ""),
            "attempts": int(payload.get("attempts") or 1),
            "sent_at": payload.get("sent") or None,
            "received_at": payload.get("received") or None,
            "payload": payload,
            "processing_status": "queued",
        }
        response = await self._request(
            "POST",
            self.notifications_table,
            json_body=[row],
            prefer="return=minimal",
            allow_conflict=True,
        )
        return response.status_code != 409

    async def notification_status(self, event_key: str) -> str | None:
        response = await self._request(
            "GET",
            self.notifications_table,
            params={
                "select": "processing_status",
                "event_key": f"eq.{event_key}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return None
        return str(rows[0].get("processing_status") or "") or None

    async def mark_notification(
        self,
        event_key: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "processing_status": status,
            "processing_error": error,
        }
        if status == "processed":
            payload["processed_at"] = datetime.now(UTC).isoformat()
        await self._request(
            "PATCH",
            self.notifications_table,
            params={"event_key": f"eq.{event_key}"},
            json_body=payload,
            prefer="return=minimal",
        )

    async def _upsert(
        self,
        table_name: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
    ) -> None:
        if not rows:
            return
        await self._request(
            "POST",
            table_name,
            params={"on_conflict": on_conflict},
            json_body=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    async def upsert_pack(self, row: dict[str, Any]) -> None:
        await self._upsert(self.packs_table, [row], on_conflict="pack_id")

    async def upsert_order(
        self,
        order_row: dict[str, Any],
        item_rows: list[dict[str, Any]],
    ) -> None:
        await self._upsert(self.orders_table, [order_row], on_conflict="order_id")
        await self._upsert(
            self.order_items_table,
            item_rows,
            on_conflict="order_id,line_number",
        )

        order_id = str(order_row["order_id"])
        stale_filter = "gte.0" if not item_rows else f"gte.{len(item_rows)}"
        await self._request(
            "DELETE",
            self.order_items_table,
            params={
                "order_id": f"eq.{order_id}",
                "line_number": stale_filter,
            },
            prefer="return=minimal",
        )

    async def upsert_shipment(self, row: dict[str, Any]) -> None:
        await self._upsert(
            self.shipments_table,
            [row],
            on_conflict="shipment_id",
        )

    async def link_order_shipment(
        self,
        *,
        order_id: str,
        shipment_id: str,
        relation_type: str = "forward",
    ) -> None:
        await self._upsert(
            self.order_shipments_table,
            [
                {
                    "order_id": order_id,
                    "shipment_id": shipment_id,
                    "relation_type": relation_type,
                }
            ],
            on_conflict="order_id,shipment_id,relation_type",
        )

    async def _list_all(
        self,
        table_name: str,
        *,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params = {
                **params,
                "limit": str(self.READ_PAGE_SIZE),
                "offset": str(offset),
            }
            response = await self._request("GET", table_name, params=page_params)
            page = response.json()
            if not isinstance(page, list):
                raise RuntimeError(f"Supabase devolvio datos invalidos desde {table_name}.")
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < self.READ_PAGE_SIZE:
                return rows
            offset += self.READ_PAGE_SIZE

    @staticmethod
    def _in_filter(values: list[str]) -> str:
        safe_values = []
        for value in values:
            text = str(value)
            if not text.isdigit():
                raise ValueError("Los IDs de Mercado Libre deben ser numericos.")
            safe_values.append(text)
        return f"in.({','.join(safe_values)})"

    async def _list_by_ids(
        self,
        table_name: str,
        *,
        filter_column: str,
        values: list[str],
        select: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for start in range(0, len(values), 100):
            chunk = values[start : start + 100]
            if not chunk:
                continue
            result.extend(
                await self._list_all(
                    table_name,
                    params={
                        "select": select,
                        filter_column: self._in_filter(chunk),
                    },
                )
            )
        return result

    async def _list_pickings(
        self,
        *,
        seller_id: str,
        sale_ids: list[str],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        try:
            for start in range(0, len(sale_ids), 100):
                chunk = sale_ids[start : start + 100]
                if not chunk:
                    continue
                result.extend(
                    await self._list_all(
                        self.sale_pickings_table,
                        params={
                            "select": (
                                "seller_id,sale_id,status,last_scanned_sku,"
                                "started_at,packed_at,updated_at"
                            ),
                            "seller_id": f"eq.{seller_id}",
                            "sale_id": self._in_filter(chunk),
                        },
                    )
                )
        except RuntimeError:
            logger.warning(
                "[SUPABASE_MELI_SALES][PICKINGS_UNAVAILABLE] "
                "Aplica la migracion de meli_sale_pickings."
            )
            return []
        return result

    async def set_sale_picking_status(
        self,
        *,
        seller_id: str,
        sale_id: str,
        status: str,
        scanned_sku: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"in_preparation", "packed"}:
            raise ValueError("Estado de picking no soportado.")

        orders = await self._list_all(
            self.orders_table,
            params={
                "select": "order_id",
                "seller_id": f"eq.{seller_id}",
                "sale_id": f"eq.{sale_id}",
            },
        )
        order_ids = [str(order["order_id"]) for order in orders]
        if not order_ids:
            raise LookupError("La venta no existe para el vendedor conectado.")

        normalized_sku = str(scanned_sku or "").strip()
        if status == "in_preparation":
            if not normalized_sku:
                raise ValueError("Debes validar un SKU para iniciar el picking.")
            items = await self._list_by_ids(
                self.order_items_table,
                filter_column="order_id",
                values=order_ids,
                select="order_id,sku",
            )
            belongs_to_sale = any(
                str(item.get("sku") or "").strip().casefold()
                == normalized_sku.casefold()
                for item in items
            )
            if not belongs_to_sale:
                raise ValueError("El SKU no pertenece a esta venta.")

        existing_response = await self._request(
            "GET",
            self.sale_pickings_table,
            params={
                "select": "started_at,last_scanned_sku",
                "seller_id": f"eq.{seller_id}",
                "sale_id": f"eq.{sale_id}",
                "limit": "1",
            },
        )
        existing_rows = existing_response.json()
        existing = (
            existing_rows[0]
            if isinstance(existing_rows, list) and existing_rows
            else {}
        )
        now = datetime.now(UTC).isoformat()
        row = {
            "seller_id": seller_id,
            "sale_id": sale_id,
            "status": status,
            "last_scanned_sku": (
                normalized_sku
                or str(existing.get("last_scanned_sku") or "").strip()
                or None
            ),
            "started_at": existing.get("started_at") or now,
            "packed_at": now if status == "packed" else None,
            "updated_at": now,
        }
        await self._upsert(
            self.sale_pickings_table,
            [row],
            on_conflict="seller_id,sale_id",
        )
        return row

    async def list_sales(
        self,
        *,
        seller_id: str,
        range_start: datetime,
        range_end: datetime,
    ) -> list[dict[str, Any]]:
        orders = await self._list_all(
            self.orders_table,
            params={
                "select": (
                    "order_id,sale_id,pack_id,shipment_id,status,date_created,"
                    "date_closed,last_updated,total_amount,paid_amount,currency_id,"
                    "sale_note,notes_error"
                ),
                "seller_id": f"eq.{seller_id}",
                "status": "eq.paid",
                "date_closed": f"gte.{range_start.isoformat()}",
                "and": f"(date_closed.lt.{range_end.isoformat()})",
                "order": "date_closed.desc,order_id.desc",
            },
        )
        if not orders:
            return []

        order_ids = [str(order["order_id"]) for order in orders]
        items = await self._list_by_ids(
            self.order_items_table,
            filter_column="order_id",
            values=order_ids,
            select=(
                "order_id,line_number,item_id,user_product_id,variation_id,sku,"
                "title,quantity,unit_price,currency_id"
            ),
        )

        shipment_ids = sorted(
            {
                str(order["shipment_id"])
                for order in orders
                if order.get("shipment_id") is not None
            }
        )
        shipments = await self._list_by_ids(
            self.shipments_table,
            filter_column="shipment_id",
            values=shipment_ids,
            select=(
                "shipment_id,status,substatus,mode,logistic_type,shipping_type,"
                "tracking_number,last_updated"
            ),
        )
        sale_ids = sorted(
            {str(order.get("sale_id") or order["order_id"]) for order in orders}
        )
        pickings = await self._list_pickings(
            seller_id=seller_id,
            sale_ids=sale_ids,
        )
        items_by_order: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            items_by_order.setdefault(str(item["order_id"]), []).append(item)
        for order_items in items_by_order.values():
            order_items.sort(key=lambda item: int(item.get("line_number") or 0))

        shipments_by_id = {
            str(shipment["shipment_id"]): shipment for shipment in shipments
        }
        pickings_by_sale_id = {
            str(picking["sale_id"]): picking for picking in pickings
        }
        grouped: dict[str, dict[str, Any]] = {}
        for order in orders:
            order_id = str(order["order_id"])
            sale_id = str(order.get("sale_id") or order_id)
            sale = grouped.setdefault(
                sale_id,
                {
                    "sale_id": sale_id,
                    "mlc": sale_id,
                    "pack_id": order.get("pack_id"),
                    "shipment_id": order.get("shipment_id"),
                    "order_id": order_id,
                    "order_ids": [],
                    "status": order.get("status") or "",
                    "date_closed": order.get("date_closed") or "",
                    "currency_id": order.get("currency_id"),
                    "total_amount": 0.0,
                    "sale_note": "",
                    "notes_error": False,
                    "items": [],
                },
            )
            sale["order_ids"].append(order_id)
            sale["total_amount"] += float(order.get("total_amount") or 0)
            sale["notes_error"] = bool(sale["notes_error"] or order.get("notes_error"))
            note = str(order.get("sale_note") or "").strip()
            if note:
                existing_notes = [entry for entry in sale["sale_note"].split(" | ") if entry]
                if note not in existing_notes:
                    sale["sale_note"] = " | ".join([*existing_notes, note])
            sale["items"].extend(items_by_order.get(order_id, []))

            shipment_id = str(order.get("shipment_id") or "")
            shipment = shipments_by_id.get(shipment_id)
            if shipment:
                sale["shipment"] = shipment
                sale["shipping_type"] = shipment.get("shipping_type") or "normal"
                sale["logistic_type"] = shipment.get("logistic_type")
                sale["shipping_status"] = shipment.get("status")
                sale["shipping_substatus"] = shipment.get("substatus")
                sale["is_dispatched"] = is_shipment_dispatched(
                    shipment.get("status"),
                    shipment.get("substatus"),
                )
            elif shipment_id:
                sale["shipping_type"] = "pending"
                sale["logistic_type"] = None
                sale["shipping_status"] = None
                sale["shipping_substatus"] = None
                sale["is_dispatched"] = None
            else:
                sale["shipping_type"] = "no_shipping"
                sale["logistic_type"] = None
                sale["shipping_status"] = None
                sale["shipping_substatus"] = None
                sale["is_dispatched"] = None

        for sale_id, sale in grouped.items():
            picking = pickings_by_sale_id.get(sale_id)
            sale["picking_status"] = picking.get("status") if picking else None
            sale["picking_started_at"] = (
                picking.get("started_at") if picking else None
            )
            sale["packed_at"] = picking.get("packed_at") if picking else None
            sale["last_scanned_sku"] = (
                picking.get("last_scanned_sku") if picking else None
            )

        return list(grouped.values())


supabase_meli_sales_store = SupabaseMeliSalesStore()
