import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx
from fastapi import FastAPI
from openpyxl import load_workbook

from config import settings
from routers.refax_router import router
from services.refax_products_service import (
    RefaxProductsError,
    RefaxProductsService,
    XLSX_CONTENT_TYPE,
)


class RefaxProductsServiceTests(unittest.IsolatedAsyncioTestCase):
    def build_service(self):
        connection_service = AsyncMock()
        connection_service.get_valid_token.return_value = "private-token"
        service = RefaxProductsService(connection_service)
        return service, connection_service

    async def test_download_returns_excel_with_only_sku_price_and_stock(self):
        service, connection_service = self.build_service()
        service._request_products = AsyncMock(
            return_value=httpx.Response(
                200,
                json=[
                    {"numero_refax": "00123", "precio": 1500, "stock": "5", "nombre_producto": "Omitir"},
                    {"numero_refax": "ABC", "precio": 19.95, "stock": "Disponible"},
                    {"numero_refax": "=1+1", "precio": 0, "stock": "0"},
                    {"numero_refax": "00000", "precio": "1250.00", "stock": 6},
                ],
                headers={"content-type": "application/json; charset=utf-8"},
            )
        )

        result = await service.download()

        self.assertEqual(result.content_type, XLSX_CONTENT_TYPE)
        workbook = load_workbook(BytesIO(result.content))
        try:
            sheet = workbook["Productos"]
            self.assertEqual(list(sheet.values), [
                ("SKU", "PRECIO", "STOCK"),
                ("00123", "1500", "5"),
                ("ABC", "19.95", "Disponible"),
                ("=1+1", "0", "0"),
                ("00000", "1250", "6"),
            ])
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    self.assertEqual(cell.data_type, "s")
                    self.assertEqual(cell.number_format, "@")
            self.assertEqual(sheet.freeze_panes, "A2")
            self.assertEqual(sheet.auto_filter.ref, "A1:C5")
        finally:
            workbook.close()
        # Excel consulta esta regla, no basta con aplicar el formato Texto.
        with ZipFile(BytesIO(result.content)) as archive:
            self.assertIsNone(archive.testzip())
            sheet_xml = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            ignored = sheet_xml.find("s:ignoredErrors/s:ignoredError", namespace)
            self.assertIsNotNone(ignored)
            self.assertEqual(ignored.attrib, {"sqref": "A2:C5", "numberStoredAsText": "1"})
        connection_service.get_valid_token.assert_awaited_once()
        service._request_products.assert_awaited_once_with("private-token")

    async def test_download_renews_and_retries_an_unauthorized_token(self):
        service, connection_service = self.build_service()
        connection_service.get_valid_token.side_effect = [
            "expired-token",
            "renewed-token",
        ]
        service._request_products = AsyncMock(
            side_effect=[
                httpx.Response(401, json={"message": "Unauthorized"}),
                httpx.Response(200, json=[{"numero_refax": "ABC", "precio": 100, "stock": "Disponible"}]),
            ]
        )

        result = await service.download()

        self.assertTrue(result.content)
        connection_service.connect.assert_awaited_once()
        self.assertEqual(
            [call.args[0] for call in service._request_products.await_args_list],
            ["expired-token", "renewed-token"],
        )

    async def test_download_reports_an_upstream_error_without_leaking_body(self):
        service, _connection_service = self.build_service()
        service._request_products = AsyncMock(
            return_value=httpx.Response(
                500,
                text="internal details that must not reach the browser",
            )
        )

        with self.assertRaisesRegex(RefaxProductsError, "HTTP 500"):
            await service.download()

    async def test_invalid_payloads_fail_instead_of_downloading_error_as_excel(self):
        payloads = [
            b'<html>Gateway error</html>',
            b'{"error":"REFAX failed"}',
            b'[]',
            b'[{"numero_refax":"ABC","stock":"5"}]',
            b'[{"numero_refax":"ABC","precio":null,"stock":"5"}]',
            b'[{"numero_refax":"ABC","precio":"NaN","stock":"5"}]',
        ]
        service, _ = self.build_service()
        for payload in payloads:
            with self.subTest(payload=payload):
                service._request_products = AsyncMock(
                    return_value=httpx.Response(200, content=payload)
                )
                with self.assertRaises(RefaxProductsError):
                    await service.download()

    async def test_persistent_unauthorized_response_refreshes_only_once(self):
        service, connection_service = self.build_service()
        service._request_products = AsyncMock(return_value=httpx.Response(401))
        with self.assertRaisesRegex(RefaxProductsError, "HTTP 401"):
            await service.download()
        connection_service.connect.assert_awaited_once()
        self.assertEqual(service._request_products.await_count, 2)

    async def test_transport_failure_retries_with_same_code_and_bearer(self):
        service, connection_service = self.build_service()
        requests = []

        async def handle(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadError("connection interrupted", request=request)
            return httpx.Response(200, json=[
                {"numero_refax": "00123", "precio": 100, "stock": "Disponible"}
            ])

        client_class = httpx.AsyncClient
        def client_factory(**kwargs):
            return client_class(transport=httpx.MockTransport(handle), **kwargs)

        with (
            patch("services.refax_products_service.httpx.AsyncClient", side_effect=client_factory),
            patch("services.refax_products_service.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await service.download()

        self.assertEqual(result.content_type, XLSX_CONTENT_TYPE)
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/Productos/Listado")
            self.assertEqual(request.url.params["codigo"], settings.refax_provider_code)
            self.assertEqual(request.headers["Authorization"], "Bearer private-token")
        sleep.assert_awaited_once_with(1.0)
        connection_service.connect.assert_not_awaited()

    async def test_timeouts_stop_after_three_attempts_without_leaking_secrets(self):
        service, _ = self.build_service()
        with (
            patch("services.refax_products_service.httpx.AsyncClient") as client,
            patch("services.refax_products_service.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            get = client.return_value.__aenter__.return_value.get
            get.side_effect = httpx.ReadTimeout("private-token in unsafe error message")
            with self.assertLogs("services.refax_products_service", level="WARNING") as logs:
                with self.assertRaises(RefaxProductsError) as raised:
                    await service.download()
        self.assertIn("tiempo de espera", str(raised.exception))
        self.assertNotIn("private-token", str(raised.exception))
        self.assertNotIn("private-token", " ".join(logs.output))
        self.assertIn("ReadTimeout", " ".join(logs.output))
        self.assertEqual(get.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_temporary_http_errors_retry_but_permanent_errors_do_not(self):
        service, _ = self.build_service()
        with (
            patch("services.refax_products_service.httpx.AsyncClient") as client,
            patch("services.refax_products_service.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            get = client.return_value.__aenter__.return_value.get
            get.side_effect = [
                httpx.Response(503),
                httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(200, json=[]),
            ]
            result = await service._request_products("private-token")
            self.assertEqual(result.status_code, 200)
            self.assertEqual([call.args[0] for call in sleep.await_args_list], [1.0, 3.0])
            get.reset_mock(side_effect=True)
            get.return_value = httpx.Response(400)
            result = await service._request_products("private-token")
            self.assertEqual(result.status_code, 400)
            get.assert_awaited_once()

    async def test_route_serves_xlsx_attachment_and_json_error_on_failure(self):
        service, _ = self.build_service()
        service._request_products = AsyncMock(return_value=httpx.Response(
            200, json=[{"numero_refax": "ABC", "precio": 100, "stock": "Disponible"}]
        ))
        app = FastAPI()
        app.include_router(router)
        with patch("routers.refax_router.refax_products_service", service):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/refax/products/download")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], XLSX_CONTENT_TYPE)
                self.assertTrue(response.headers["content-disposition"].endswith('.xlsx"'))
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertTrue(response.content.startswith(b"PK"))
                service._request_products = AsyncMock(return_value=httpx.Response(200, json={"error": "bad"}))
                response = await client.get("/refax/products/download")
                self.assertEqual(response.status_code, 502)
                self.assertIn("detail", response.json())
                self.assertNotIn("content-disposition", response.headers)


if __name__ == "__main__":
    unittest.main()
