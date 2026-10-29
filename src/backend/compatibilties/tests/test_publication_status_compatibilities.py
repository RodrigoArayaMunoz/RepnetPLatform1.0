import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from config import settings
from services.publication_sync_service import PublicationSyncService
from services.ml_publication_fields import (
    extract_publication_has_compatibilities,
    extract_publication_status,
)
from services.supabase_publications_store import SupabasePublicationsStore


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
    async def test_manual_sync_preserves_status_and_false_through_supabase_write(self):
        service = PublicationSyncService()
        service._request_ml = AsyncMock(return_value=[{"code": 200, "body": {
            "id": "MLC123", "seller_id": 99, "title": "Repuesto",
            "date_created": "2026-10-01T10:00:00Z",
            "status": "paused", "attributes": [],
            "tags": ["incomplete_compatibilities"],
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
            self.assertEqual(await store.upsert_rows(rows), 1)
        self.assertEqual(posts[0]["status"], "paused")
        self.assertIs(posts[0]["has_compatibilities"], False)
        self.assertEqual(posts[0]["seller_id"], 99)
        self.assertEqual(posts[0]["sync_run_id"], "run-123")

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
