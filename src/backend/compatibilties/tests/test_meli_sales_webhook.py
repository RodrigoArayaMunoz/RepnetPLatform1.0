import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from config import settings
from main import app
from routers.mercadolibre_webhook_router import (
    notification_event_key,
    validate_meli_notification,
)
from services.meli_sales_sync_service import MeliSalesSyncService
from services.supabase_meli_sales_store import (
    SupabaseMeliSalesStore,
    is_shipment_dispatched,
)


class MercadoLibreWebhookValidationTests(unittest.TestCase):
    def test_endpoint_is_public_and_queues_valid_notification(self):
        payload = {
            "_id": "event-public-route",
            "application_id": "1234",
            "user_id": 99,
            "topic": "shipments",
            "resource": "/shipments/40000000001",
            "attempts": 1,
        }
        with (
            patch.object(settings, "ml_client_id", "1234"),
            patch.object(settings, "ml_notification_application_id", None),
            patch.object(settings, "ml_notification_allowed_user_id", None),
            patch(
                "routers.mercadolibre_webhook_router."
                "process_meli_notification_task.apply_async",
                return_value=SimpleNamespace(id="task-1"),
            ) as enqueue,
        ):
            response = TestClient(app).post(
                "/webhooks/mercadolibre",
                json=payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        enqueue.assert_called_once()

    def test_accepts_only_configured_app_and_supported_resource(self):
        payload = {
            "_id": "event-1",
            "application_id": "1234",
            "user_id": 99,
            "topic": "orders_v2",
            "resource": "/orders/2000000000000001",
            "attempts": 1,
            "sent": "2026-08-28T10:00:00Z",
        }
        with (
            patch.object(settings, "ml_client_id", "1234"),
            patch.object(settings, "ml_notification_application_id", None),
            patch.object(settings, "ml_notification_allowed_user_id", None),
        ):
            normalized = validate_meli_notification(payload)

        self.assertEqual(normalized["user_id"], "99")
        self.assertEqual(normalized["resource"], "/orders/2000000000000001")

        with (
            patch.object(settings, "ml_client_id", "other"),
            patch.object(settings, "ml_notification_application_id", None),
            patch.object(settings, "ml_notification_allowed_user_id", None),
        ):
            with self.assertRaises(HTTPException) as context:
                validate_meli_notification(payload)
        self.assertEqual(context.exception.status_code, 403)

    def test_retries_without_id_have_the_same_idempotency_key(self):
        base = {
            "application_id": "1234",
            "user_id": "99",
            "topic": "shipments",
            "resource": "/shipments/40000000001",
            "sent": "2026-08-28T10:00:00Z",
        }
        self.assertEqual(
            notification_event_key({**base, "attempts": 1}),
            notification_event_key({**base, "attempts": 8}),
        )


class MercadoLibreShipmentDispatchTests(unittest.TestCase):
    def test_classifies_shipment_states_from_mercado_libre(self):
        self.assertFalse(is_shipment_dispatched("pending"))
        self.assertFalse(is_shipment_dispatched("handling"))
        self.assertFalse(is_shipment_dispatched("ready_to_ship", "printed"))
        self.assertTrue(is_shipment_dispatched("shipped"))
        self.assertTrue(is_shipment_dispatched("delivered"))
        self.assertTrue(is_shipment_dispatched("not_delivered"))
        self.assertIsNone(is_shipment_dispatched("cancelled"))
        self.assertIsNone(is_shipment_dispatched(None))

    def test_cross_docking_pickup_is_already_dispatched(self):
        self.assertTrue(is_shipment_dispatched("ready_to_ship", "picked_up"))
        self.assertTrue(is_shipment_dispatched("ready_to_ship", "in_hub"))


class MercadoLibreSalesNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stored_sales_groups_pack_orders_and_items(self):
        store = SupabaseMeliSalesStore()
        orders = [
            {
                "order_id": "1",
                "sale_id": "100",
                "pack_id": "100",
                "shipment_id": "500",
                "status": "paid",
                "date_closed": "2026-08-28T10:00:00-04:00",
                "total_amount": 1000,
                "sale_note": "Nota",
                "notes_error": False,
            },
            {
                "order_id": "2",
                "sale_id": "100",
                "pack_id": "100",
                "shipment_id": "500",
                "status": "paid",
                "date_closed": "2026-08-28T10:00:00-04:00",
                "total_amount": 2000,
                "sale_note": "",
                "notes_error": False,
            },
        ]
        items = [
            {"order_id": "1", "line_number": 0, "sku": "A", "quantity": 2},
            {"order_id": "2", "line_number": 0, "sku": "B", "quantity": 1},
        ]
        shipments = [
            {
                "shipment_id": "500",
                "shipping_type": "flex",
                "logistic_type": "self_service",
                "status": "ready_to_ship",
            }
        ]

        async def list_by_ids(table_name, **_kwargs):
            if table_name == store.order_items_table:
                return items
            if table_name == store.shipments_table:
                return shipments
            raise AssertionError(table_name)

        with (
            patch.object(store, "_list_all", AsyncMock(return_value=orders)),
            patch.object(store, "_list_by_ids", AsyncMock(side_effect=list_by_ids)),
            patch.object(
                store,
                "_list_pickings",
                AsyncMock(
                    return_value=[
                        {
                            "seller_id": "99",
                            "sale_id": "100",
                            "status": "in_preparation",
                            "last_scanned_sku": "A",
                            "started_at": "2026-08-28T10:05:00-04:00",
                            "packed_at": None,
                        }
                    ]
                ),
            ),
        ):
            sales = await store.list_sales(
                seller_id="99",
                range_start=datetime.fromisoformat("2026-08-28T00:00:00-04:00"),
                range_end=datetime.fromisoformat("2026-08-29T00:00:00-04:00"),
            )

        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["order_ids"], ["1", "2"])
        self.assertEqual([item["sku"] for item in sales[0]["items"]], ["A", "B"])
        self.assertEqual(sales[0]["shipping_type"], "flex")
        self.assertFalse(sales[0]["is_dispatched"])
        self.assertEqual(sales[0]["picking_status"], "in_preparation")
        self.assertEqual(sales[0]["last_scanned_sku"], "A")
        self.assertEqual(sales[0]["total_amount"], 3000)

    async def test_valid_sku_starts_picking_and_persists_status(self):
        store = SupabaseMeliSalesStore()
        with (
            patch.object(
                store,
                "_list_all",
                AsyncMock(return_value=[{"order_id": "1"}]),
            ),
            patch.object(
                store,
                "_list_by_ids",
                AsyncMock(return_value=[{"order_id": "1", "sku": "SKU-ONE"}]),
            ),
            patch.object(
                store,
                "_request",
                AsyncMock(return_value=SimpleNamespace(json=lambda: [])),
            ),
            patch.object(store, "_upsert", AsyncMock()) as upsert,
        ):
            result = await store.set_sale_picking_status(
                seller_id="99",
                sale_id="100",
                status="in_preparation",
                scanned_sku="sku-one",
            )

        self.assertEqual(result["status"], "in_preparation")
        self.assertEqual(result["last_scanned_sku"], "sku-one")
        upsert.assert_awaited_once()

    async def test_rejects_sku_that_does_not_belong_to_sale(self):
        store = SupabaseMeliSalesStore()
        with (
            patch.object(
                store,
                "_list_all",
                AsyncMock(return_value=[{"order_id": "1"}]),
            ),
            patch.object(
                store,
                "_list_by_ids",
                AsyncMock(return_value=[{"order_id": "1", "sku": "SKU-ONE"}]),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "no pertenece"):
                await store.set_sale_picking_status(
                    seller_id="99",
                    sale_id="100",
                    status="in_preparation",
                    scanned_sku="SKU-WRONG",
                )

    async def test_hydrates_all_pack_orders_with_skus_quantities_and_flex(self):
        service = MeliSalesSyncService()
        order_one = {
            "id": 2000000000000001,
            "pack_id": 2000000000000100,
            "status": "paid",
            "date_closed": "2026-08-28T10:00:00-04:00",
            "seller": {"id": 99},
            "shipping": {"id": 40000000001},
            "order_items": [
                {
                    "item": {
                        "id": "MLC1",
                        "title": "Producto uno",
                        "seller_sku": "SKU-ONE",
                    },
                    "quantity": 2,
                    "unit_price": 1000,
                    "currency_id": "CLP",
                }
            ],
        }
        order_two = {
            "id": 2000000000000002,
            "pack_id": 2000000000000100,
            "status": "paid",
            "date_closed": "2026-08-28T10:00:00-04:00",
            "seller": {"id": 99},
            "shipping": {"id": 40000000001},
            "order_items": [
                {
                    "item": {
                        "id": "MLC2",
                        "title": "Producto dos",
                        "seller_custom_field": "SKU-TWO",
                    },
                    "quantity": 1,
                    "unit_price": 2000,
                    "currency_id": "CLP",
                }
            ],
        }
        pack_payload = {
            "id": 2000000000000100,
            "shipment": {"id": 40000000001},
            "orders": [
                {"id": 2000000000000001},
                {"id": 2000000000000002},
            ],
            "status": "released",
        }
        shipment = {
            "id": 40000000001,
            "sender_id": 99,
            "pack_id": 2000000000000100,
            "order_id": 2000000000000001,
            "type": "forward",
            "status": "ready_to_ship",
            "mode": "me2",
            "logistic_type": "self_service",
        }

        async def request_ml(path, *, user_id, params=None):
            del user_id, params
            responses = {
                "/orders/2000000000000001": order_one,
                "/orders/2000000000000002": order_two,
                "/packs/2000000000000100": pack_payload,
                "/shipments/40000000001": shipment,
                "/orders/2000000000000001/notes": [{"note": "Nota 1"}],
                "/orders/2000000000000002/notes": [],
            }
            return responses[path]

        saved_orders = []
        saved_shipments = []

        async def save_order(order_row, item_rows):
            saved_orders.append((order_row, item_rows))

        with (
            patch.object(service, "_request_ml", AsyncMock(side_effect=request_ml)),
            patch(
                "services.meli_sales_sync_service.supabase_meli_sales_store.upsert_pack",
                new=AsyncMock(),
            ),
            patch(
                "services.meli_sales_sync_service.supabase_meli_sales_store.upsert_order",
                new=AsyncMock(side_effect=save_order),
            ),
            patch(
                "services.meli_sales_sync_service.supabase_meli_sales_store.upsert_shipment",
                new=AsyncMock(side_effect=lambda row: saved_shipments.append(row)),
            ),
            patch(
                "services.meli_sales_sync_service.supabase_meli_sales_store.link_order_shipment",
                new=AsyncMock(),
            ),
        ):
            order_ids = await service.hydrate_order(
                order_id="2000000000000001",
                user_id="99",
            )

        self.assertEqual(
            order_ids,
            {"2000000000000001", "2000000000000002"},
        )
        self.assertEqual(saved_orders[0][1][0]["sku"], "SKU-ONE")
        self.assertEqual(saved_orders[0][1][0]["quantity"], 2)
        self.assertEqual(saved_orders[1][1][0]["sku"], "SKU-TWO")
        self.assertEqual(saved_shipments[0]["shipping_type"], "flex")


if __name__ == "__main__":
    unittest.main()
