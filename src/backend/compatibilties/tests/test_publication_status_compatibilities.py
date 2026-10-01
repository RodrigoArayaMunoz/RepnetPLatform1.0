import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from config import settings
from services.meli_sales_sync_service import MeliSalesSyncService
from services.ml_publication_fields import (
    extract_publication_has_compatibilities,
    extract_publication_status,
)
from services.supabase_publications_store import SupabasePublicationsStore
from tasks.meli_sales_tasks import _process_notification


class PublicationMetadataTests(unittest.TestCase):
    def test_status_preserves_the_original_value(self):
        for status in ("active", "paused", "closed", "under_review", "new_status"):
            with self.subTest(status=status):
                self.assertEqual(extract_publication_status({"status": f" {status} "}), status)
        self.assertIsNone(extract_publication_status({}))
        self.assertIsNone(extract_publication_status({"status": " "}))

    def test_compatibility_signals_and_missing_evidence(self):
        cases = [
            ({"attributes": [None, {"id": "PART_NUMBER"}, {
                "id": "HAS_COMPATIBILITIES", "value_id": "242085", "value_name": "Sí",
            }], "tags": []}, True),
            ({"attributes": [{"id": "HAS_COMPATIBILITIES"}]}, True),
            ({"attributes": [{"id": "HAS_COMPATIBILITIES", "value_name": " No "}]}, False),
            ({"attributes": [], "tags": ["user_product_listing", "incomplete_compatibilities"]}, False),
            ({"tags": ["incomplete_compatibilities"]}, False),
            # An incomplete tag may coexist with already registered compatibilities.
            ({"attributes": [{"id": "HAS_COMPATIBILITIES", "value_name": "Sí"}],
              "tags": ["incomplete_compatibilities"]}, True),
            ({"attributes": [], "tags": []}, None),
            ({}, None),
            ({"attributes": "HAS_COMPATIBILITIES", "tags": "incomplete_compatibilities"}, None),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                self.assertIs(extract_publication_has_compatibilities(item), expected)


class PublicationMetadataPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_is_processed_only_after_status_and_false_are_stored(self):
        stored_cases = [
            {"status": "paused", "has_compatibilities": False},
            {"status": None, "has_compatibilities": None},
            {"status": "active", "has_compatibilities": False},
            {"status": "paused", "has_compatibilities": True},
            {"status": "paused", "has_compatibilities": None},
        ]
        for stored_fields in stored_cases:
            with self.subTest(stored_fields=stored_fields):
                service = MeliSalesSyncService()
                service.start_request_context = Mock()
                service.close_request_context = AsyncMock()
                service._request_ml = AsyncMock(return_value={
                    "id": "MLC123", "seller_id": 99, "title": "Repuesto",
                    "date_created": "2026-10-01T10:00:00Z",
                    "status": "paused", "attributes": [],
                    "tags": ["incomplete_compatibilities"],
                })
                store = SupabasePublicationsStore()
                store._headers = Mock(return_value={})
                posts = []

                def handle_request(request):
                    if request.method == "POST":
                        posts.append(json.loads(request.content))
                        return httpx.Response(200, json=1)
                    return httpx.Response(200, json=[{"mlc": "MLC123", **stored_fields}])

                notifications = Mock()
                notifications.register_notification = AsyncMock()
                notifications.notification_status = AsyncMock(return_value="queued")
                notifications.mark_notification = AsyncMock()
                client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
                success = stored_fields == {"status": "paused", "has_compatibilities": False}
                with (
                    patch.object(settings, "supabase_url", "https://database.example.test"),
                    patch("tasks.meli_sales_tasks._prepare_ml_user", new=AsyncMock()),
                    patch("tasks.meli_sales_tasks.ml_client.startup", new=AsyncMock()),
                    patch("tasks.meli_sales_tasks.ml_client.shutdown", new=AsyncMock()),
                    patch("tasks.meli_sales_tasks.MeliSalesSyncService", return_value=service),
                    patch("tasks.meli_sales_tasks.supabase_meli_sales_store", notifications),
                    patch("services.meli_sales_sync_service.supabase_publications_store", store),
                    patch("services.supabase_publications_store.httpx.AsyncClient", return_value=client),
                ):
                    payload = {"topic": "items", "resource": "/items/MLC123", "user_id": "99"}
                    if success:
                        await _process_notification(payload, "event-metadata")
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Supabase no guardo"):
                            await _process_notification(payload, "event-metadata")

                row = posts[0]["publication_rows"][0]
                self.assertEqual(row["status"], "paused")
                self.assertIs(row["has_compatibilities"], False)
                states = [call.kwargs["status"] for call in notifications.mark_notification.await_args_list]
                self.assertEqual(states, ["processing", "processed"] if success else ["processing"])
                service.close_request_context.assert_awaited_once()

    async def test_export_query_reads_new_fields_with_seller_filter(self):
        store = SupabasePublicationsStore()
        store._headers = Mock(return_value={})
        expected = [{"mlc": "MLC123", "status": "active", "has_compatibilities": True}]

        def handle_request(request):
            self.assertEqual(request.url.params["seller_id"], "eq.99")
            self.assertEqual(request.url.params["fecha_creacion"], "eq.2026-10-01")
            columns = request.url.params["select"].split(",")
            self.assertIn("status", columns)
            self.assertIn("has_compatibilities", columns)
            return httpx.Response(200, json=expected)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch("services.supabase_publications_store.httpx.AsyncClient", return_value=client),
        ):
            rows = await store.list_by_creation_date("2026-10-01", seller_id="99")
        self.assertEqual(rows, expected)


if __name__ == "__main__":
    unittest.main()
