import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from config import settings
from services.meli_sales_sync_service import MeliSalesSyncService
from services.ml_publication_fields import extract_publication_part_number
from services.supabase_publications_store import SupabasePublicationsStore
from tasks.meli_sales_tasks import _process_notification


class ItemPartNumberTests(unittest.IsolatedAsyncioTestCase):
    def test_part_number_is_the_textual_value_not_the_attribute_value_id(self):
        self.assertEqual(
            extract_publication_part_number({"attributes": [{
                "id": "PART_NUMBER", "value_id": "38452372", "value_name": " 00123-A ",
            }]}),
            "00123-A",
        )
        self.assertIsNone(extract_publication_part_number({"attributes": []}))
        self.assertIsNone(extract_publication_part_number({"attributes": [{
            "id": "PART_NUMBER", "value_id": "-1", "value_name": None,
        }]}))

    async def test_notification_is_processed_only_after_part_number_is_persisted(self):
        for stored_part_number in ("00123-A", None):
            with self.subTest(stored_part_number=stored_part_number):
                service = MeliSalesSyncService()
                service.start_request_context = Mock()
                service.close_request_context = AsyncMock()
                service._request_ml = AsyncMock(return_value={
                    "id": "MLC123", "seller_id": 99, "title": "Repuesto",
                    "date_created": "2026-10-01T10:00:00Z",
                    "attributes": [{"id": "PART_NUMBER", "value_name": "00123-A"}],
                })
                store = SupabasePublicationsStore()
                store._headers = Mock(return_value={})
                posts = []

                def handle_request(request):
                    if request.method == "POST":
                        posts.append(json.loads(request.content))
                        return httpx.Response(200, json=1)
                    return httpx.Response(200, json=[{
                        "mlc": "MLC123", "part_number": stored_part_number,
                    }])

                client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
                notifications = Mock()
                notifications.register_notification = AsyncMock()
                notifications.notification_status = AsyncMock(return_value="queued")
                notifications.mark_notification = AsyncMock()

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
                    if stored_part_number is None:
                        with self.assertRaisesRegex(RuntimeError, "no guardo el PART_NUMBER"):
                            await _process_notification(payload, "event-123")
                    else:
                        await _process_notification(payload, "event-123")

                self.assertEqual(posts[0]["publication_rows"][0]["part_number"], "00123-A")
                states = [call.kwargs["status"] for call in notifications.mark_notification.await_args_list]
                self.assertEqual(states, ["processing"] if stored_part_number is None else ["processing", "processed"])
                service.close_request_context.assert_awaited_once()

    async def test_a_different_sellers_item_is_not_persisted(self):
        service = MeliSalesSyncService()
        with (
            patch.object(service, "_request_ml", AsyncMock(return_value={
                "id": "MLC123", "seller_id": 100, "attributes": [],
            })),
            patch("services.meli_sales_sync_service.supabase_publications_store.upsert_incremental_rows", new=AsyncMock()) as save,
            self.assertRaisesRegex(ValueError, "no pertenece"),
        ):
            await service.hydrate_publication(item_id="MLC123", user_id="99")
        save.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
