import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from config import settings
from services.seller_sales_service import SellerSalesService


class SellerSalesServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_all_paid_orders_and_their_seller_notes(self):
        service = SellerSalesService()
        calls = []

        async def request_ml(method, path, *, user_id, params=None):
            calls.append((method, path, user_id, params))
            if path == "/users/me":
                return {
                    "id": 2682261950,
                    "nickname": "REPNET",
                    "site_id": "MLC",
                }
            if path == "/orders/search" and params["offset"] == 0:
                return {
                    "paging": {"total": 2, "offset": 0, "limit": 1},
                    "results": [
                        {
                            "id": 2000000000000001,
                            "status": "paid",
                            "date_closed": "2026-08-10T09:30:00.000-04:00",
                            "pack_id": 2000000000000101,
                        }
                    ],
                }
            if path == "/orders/search":
                return {
                    "paging": {"total": 2, "offset": 1, "limit": 1},
                    "results": [
                        {
                            "id": 2000000000000002,
                            "status": "paid",
                            "date_closed": "2026-08-10T18:05:00.000-04:00",
                            "pack_id": None,
                        }
                    ],
                }
            if path.endswith("0001/notes"):
                return [{"note": "Nota de venta 101"}]
            if path.endswith("0002/notes"):
                return {
                    "results": [
                        {"note": "Nota de venta 102"},
                        {"note": "Preparar retiro"},
                    ]
                }
            raise AssertionError(f"Llamada inesperada: {path}")

        service._request_ml = AsyncMock(side_effect=request_ml)
        with (
            patch.object(settings, "seller_sales_page_limit", 1),
            patch.object(settings, "seller_sales_note_concurrency", 2),
        ):
            result = await service.get_daily_sales(
                user_id="2682261950",
                sales_date=date(2026, 8, 8),
                sales_date_to=date(2026, 8, 10),
            )

        self.assertEqual(result["seller"]["id"], "2682261950")
        self.assertEqual(result["date_from"], "2026-08-08")
        self.assertEqual(result["date_to"], "2026-08-10")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["notes_failed"], 0)
        self.assertEqual(result["sales"][0]["mlc"], "2000000000000001")
        self.assertEqual(result["sales"][0]["sale_note"], "Nota de venta 101")
        self.assertEqual(
            result["sales"][1]["sale_note"],
            "Nota de venta 102 | Preparar retiro",
        )

        search_calls = [call for call in calls if call[1] == "/orders/search"]
        self.assertEqual([call[3]["offset"] for call in search_calls], [0, 1])
        first_params = search_calls[0][3]
        self.assertEqual(first_params["seller"], "2682261950")
        self.assertEqual(first_params["order.status"], "paid")
        self.assertEqual(
            first_params["order.date_closed.from"],
            "2026-08-08T00:00:00.000-04:00",
        )
        self.assertEqual(
            first_params["order.date_closed.to"],
            "2026-08-11T00:00:00.000-04:00",
        )

    async def test_applies_strict_chile_date_and_paid_status_filter(self):
        service = SellerSalesService()

        async def request_ml(_method, path, *, user_id, params=None):
            del user_id, params
            if path == "/users/me":
                return {"id": 99, "nickname": "REPNET", "site_id": "MLC"}
            if path == "/orders/search":
                return {
                    "paging": {"total": 4, "offset": 0, "limit": 50},
                    "results": [
                        {
                            "id": 1,
                            "status": "paid",
                            "date_closed": "2026-08-10T00:00:00.000-04:00",
                        },
                        {
                            "id": 2,
                            "status": "paid",
                            "date_closed": "2026-08-11T00:00:00.000-04:00",
                        },
                        {
                            "id": 3,
                            "status": "partially_refunded",
                            "date_closed": "2026-08-10T12:00:00.000-04:00",
                        },
                        {
                            "id": 4,
                            "status": "paid",
                            "date_closed": "2026-08-09T23:59:59.000-04:00",
                        },
                    ],
                }
            if path == "/orders/1/notes":
                return []
            raise AssertionError(f"No se debio consultar {path}")

        service._request_ml = AsyncMock(side_effect=request_ml)
        result = await service.get_daily_sales(
            user_id="99",
            sales_date=date(2026, 8, 10),
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["sales"][0]["order_id"], "1")

    async def test_keeps_sale_when_notes_endpoint_fails(self):
        service = SellerSalesService()

        async def request_ml(_method, path, *, user_id, params=None):
            del user_id, params
            if path == "/users/me":
                return {"id": 99, "nickname": "REPNET", "site_id": "MLC"}
            if path == "/orders/search":
                return {
                    "paging": {"total": 1, "offset": 0, "limit": 50},
                    "results": [
                        {
                            "id": 10,
                            "status": "paid",
                            "date_closed": "2026-08-10T12:00:00.000-04:00",
                        }
                    ],
                }
            if path == "/orders/10/notes":
                raise HTTPException(status_code=503, detail="No disponible")
            raise AssertionError(f"Llamada inesperada: {path}")

        service._request_ml = AsyncMock(side_effect=request_ml)
        result = await service.get_daily_sales(
            user_id="99",
            sales_date=date(2026, 8, 10),
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["notes_failed"], 1)
        self.assertTrue(result["sales"][0]["notes_error"])
        self.assertEqual(result["sales"][0]["sale_note"], "")


if __name__ == "__main__":
    unittest.main()
