import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from services.supabase_publications_store import SupabasePublicationsStore


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.row_counts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, *args, **kwargs):
        self.row_counts.append(len(kwargs["json"]))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SupabasePublicationsUpsertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SupabasePublicationsStore()
        self.store._headers = Mock(return_value={})
        self.rows = [
            {"seller_id": 1, "mlc": f"MLC{index}"}
            for index in range(4)
        ]

    async def _upsert(self, outcomes, rows=None):
        client = _FakeAsyncClient(outcomes)
        with (
            patch(
                "services.supabase_publications_store.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "services.supabase_publications_store.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            count = await self.store.upsert_rows(rows or self.rows)
        return count, client, sleep

    async def test_retries_a_transient_520_without_losing_the_batch(self):
        count, client, sleep = await self._upsert(
            [_FakeResponse(520, "origin error"), _FakeResponse(201)]
        )

        self.assertEqual(count, 4)
        self.assertEqual(client.row_counts, [4, 4])
        sleep.assert_awaited_once_with(1.0)

    async def test_splits_a_batch_after_transient_retries_are_exhausted(self):
        self.store.UPSERT_MAX_ATTEMPTS = 2
        self.store.UPSERT_SPLIT_MIN_BATCH_SIZE = 1

        count, client, _ = await self._upsert(
            [
                _FakeResponse(520),
                _FakeResponse(520),
                _FakeResponse(201),
                _FakeResponse(201),
            ]
        )

        self.assertEqual(count, 4)
        self.assertEqual(client.row_counts, [4, 4, 2, 2])

    async def test_does_not_retry_a_schema_error(self):
        client = _FakeAsyncClient(
            [
                _FakeResponse(
                    400,
                    '{"message":"column fecha_creacion does not exist","code":"42703"}',
                )
            ]
        )
        with (
            patch(
                "services.supabase_publications_store.httpx.AsyncClient",
                return_value=client,
            ),
            self.assertRaisesRegex(RuntimeError, "fecha_creacion"),
        ):
            await self.store.upsert_rows(self.rows)

        self.assertEqual(client.row_counts, [4])

    async def test_reports_a_persistent_520_as_transient(self):
        self.store.UPSERT_MAX_ATTEMPTS = 2
        client = _FakeAsyncClient([_FakeResponse(520), _FakeResponse(520)])
        with (
            patch(
                "services.supabase_publications_store.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "services.supabase_publications_store.asyncio.sleep",
                new=AsyncMock(),
            ),
            self.assertRaisesRegex(RuntimeError, "error temporal"),
        ):
            await self.store.upsert_rows([self.rows[0]])

    async def test_retries_a_transport_error(self):
        error = httpx.ConnectError("connection failed")
        count, client, sleep = await self._upsert(
            [error, _FakeResponse(201)]
        )

        self.assertEqual(count, 4)
        self.assertEqual(client.row_counts, [4, 4])
        sleep.assert_awaited_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
