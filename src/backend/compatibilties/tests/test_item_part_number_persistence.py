import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from config import settings
from services.ml_publication_fields import extract_publication_part_number
from services.publication_sync_service import PublicationSyncService
from services.supabase_publications_store import SupabasePublicationsStore


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

    async def test_manual_sync_keeps_part_number_through_supabase_write(self):
        for part_number in ("00123-A", None):
            with self.subTest(part_number=part_number):
                service = PublicationSyncService()
                service._request_ml = AsyncMock(return_value=[{"code": 200, "body": {
                    "id": "MLC123", "seller_id": 99, "title": "Repuesto",
                    "date_created": "2026-10-01T10:00:00Z",
                    "attributes": [{"id": "PART_NUMBER", "value_name": part_number}],
                }}])
                rows, failures = await service._fetch_multiget_chunk(
                    chunk=["MLC123"], user_id="99", seller_id="99", sync_run_id="run-123",
                )
                self.assertEqual(failures, 0)
                store = SupabasePublicationsStore()
                store._headers = Mock(return_value={})
                posts = []

                def handle_request(request):
                    self.assertEqual(request.method, "POST")
                    self.assertEqual(request.url.path, "/rest/v1/publicaciones_ml")
                    posts.extend(json.loads(request.content))
                    return httpx.Response(201)

                client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
                with (
                    patch.object(settings, "supabase_url", "https://database.example.test"),
                    patch("services.supabase_publications_store.httpx.AsyncClient", return_value=client),
                ):
                    count = await store.upsert_rows(rows)
                self.assertEqual(count, 1)
                self.assertEqual(posts[0]["part_number"], part_number)
                self.assertEqual(posts[0]["seller_id"], 99)
                self.assertEqual(posts[0]["sync_run_id"], "run-123")


if __name__ == "__main__":
    unittest.main()
