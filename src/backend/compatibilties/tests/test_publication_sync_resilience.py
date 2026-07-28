import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException

from config import settings
from services.ml_client import MercadoLibreClient, ml_client
from services.publication_sync_service import PublicationSyncService
from services.publication_sync_store import publication_sync_store
from services.supabase_publications_store import (
    supabase_publications_store,
)
from tasks.publication_sync_tasks import _sync_error_message


class _FakeRedisClient:
    async def aclose(self):
        return None


class _FakeLimiter:
    def __init__(self):
        self.client = _FakeRedisClient()
        self.acquire_count = 0
        self.penalties = []

    async def acquire(self):
        self.acquire_count += 1

    async def penalize(self, cooldown_seconds):
        self.penalties.append(cooldown_seconds)


def _response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(
            "GET",
            "https://api.mercadolibre.com/items",
        ),
    )


class MercadoLibreUnknownForbiddenRetryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_unknown_forbidden_get_is_retried_and_rate_limited(self):
        client = MercadoLibreClient()
        client.client = Mock()
        client.client.request = AsyncMock(
            side_effect=[
                _response(
                    403,
                    {
                        "message": "unknown_error",
                        "error": "forbidden",
                        "status": 403,
                        "cause": None,
                    },
                ),
                _response(200, [{"code": 200, "body": {"id": "MLC1"}}]),
            ]
        )
        limiter = _FakeLimiter()

        with (
            patch.object(
                client,
                "get_valid_token",
                new=AsyncMock(return_value="token"),
            ),
            patch.object(settings, "ml_retry_attempts", 2),
            patch.object(
                settings,
                "ml_retry_unknown_403_min_delay_seconds",
                5.0,
            ),
            patch.object(
                settings,
                "ml_retry_unknown_403_cooldown_seconds",
                15.0,
            ),
            patch(
                "services.ml_client.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
            patch("services.ml_client.random.uniform", return_value=0),
        ):
            payload = await client.request(
                "GET",
                "/items",
                user_id="2682261950",
                rate_limiter=limiter,
            )

        self.assertEqual(payload[0]["body"]["id"], "MLC1")
        self.assertEqual(limiter.acquire_count, 2)
        self.assertEqual(limiter.penalties, [15.0])
        sleep.assert_awaited_once_with(5.0)

    async def test_explicit_permission_403_is_not_retried(self):
        client = MercadoLibreClient()
        client.client = Mock()
        client.client.request = AsyncMock(
            return_value=_response(
                403,
                {
                    "message": "Caller ID must match item owner",
                    "error": "forbidden",
                    "status": 403,
                },
            )
        )
        limiter = _FakeLimiter()

        with (
            patch.object(
                client,
                "get_valid_token",
                new=AsyncMock(return_value="token"),
            ),
            patch.object(settings, "ml_retry_attempts", 3),
            patch(
                "services.ml_client.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
            self.assertRaises(HTTPException),
        ):
            await client.request(
                "GET",
                "/items",
                user_id="2682261950",
                rate_limiter=limiter,
            )

        self.assertEqual(client.client.request.await_count, 1)
        self.assertEqual(limiter.acquire_count, 1)
        self.assertEqual(limiter.penalties, [])
        sleep.assert_not_awaited()


class PublicationSyncTaskErrorTests(unittest.TestCase):
    def test_retry_exhaustion_is_presented_as_a_human_message(self):
        message = _sync_error_message(
            HTTPException(
                status_code=403,
                detail={
                    "message": "raw ML error",
                    "retryable": True,
                    "retry_reason": "unknown_forbidden",
                },
            )
        )

        self.assertIn("rechazó temporalmente", message)
        self.assertIn("Supabase se conservaron", message)
        self.assertNotIn("{", message)


class PublicationSyncResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_requests_delegate_every_attempt_to_shared_limiter(self):
        service = PublicationSyncService()
        limiter = _FakeLimiter()
        service._read_rate_limiter = limiter

        with patch.object(
            ml_client,
            "request",
            new=AsyncMock(return_value={"ok": True}),
        ) as request:
            response = await service._request_ml(
                "GET",
                "/items",
                user_id="2682261950",
                params={"ids": "MLC1"},
            )

        self.assertEqual(response, {"ok": True})
        self.assertEqual(limiter.acquire_count, 0)
        request.assert_awaited_once_with(
            "GET",
            "/items",
            params={"ids": "MLC1"},
            user_id="2682261950",
            rate_limiter=limiter,
        )

    async def test_failed_chunk_does_not_discard_successful_siblings(self):
        service = PublicationSyncService()
        service.MULTIGET_CHUNK_SIZE = 1
        service.MULTIGET_CONCURRENCY = 4
        successful_rows = [
            ([{"seller_id": 1, "mlc": f"MLC{index}"}], 0)
            for index in range(1, 4)
        ]
        chunk_error = HTTPException(
            status_code=403,
            detail={
                "message": "unknown forbidden",
                "retryable": True,
                "retry_reason": "unknown_forbidden",
            },
        )

        with (
            patch.object(
                supabase_publications_store,
                "ensure_ready",
                new=AsyncMock(),
            ),
            patch.object(
                service,
                "_resolve_seller_id",
                new=AsyncMock(return_value="1"),
            ),
            patch.object(
                service,
                "_scan_all_item_ids",
                new=AsyncMock(
                    return_value=["MLC1", "MLC2", "MLC3", "MLC4"]
                ),
            ),
            patch.object(
                service,
                "_fetch_multiget_chunk",
                new=AsyncMock(
                    side_effect=[*successful_rows, chunk_error]
                ),
            ),
            patch.object(
                supabase_publications_store,
                "upsert_rows",
                new=AsyncMock(side_effect=lambda rows: len(rows)),
            ) as upsert_rows,
            patch.object(publication_sync_store, "update", new=Mock()),
            self.assertRaises(HTTPException),
        ):
            await service.sync_publications(user_id="1")

        upsert_rows.assert_awaited_once()
        saved_rows = upsert_rows.await_args.args[0]
        self.assertEqual(
            [row["mlc"] for row in saved_rows],
            ["MLC1", "MLC2", "MLC3"],
        )

    async def test_stale_rows_are_not_deleted_when_run_count_mismatches(self):
        service = PublicationSyncService()
        service.MULTIGET_CHUNK_SIZE = 1
        service.MULTIGET_CONCURRENCY = 2

        with (
            patch.object(
                supabase_publications_store,
                "ensure_ready",
                new=AsyncMock(),
            ),
            patch.object(
                service,
                "_resolve_seller_id",
                new=AsyncMock(return_value="1"),
            ),
            patch.object(
                service,
                "_scan_all_item_ids",
                new=AsyncMock(return_value=["MLC1", "MLC2"]),
            ),
            patch.object(
                service,
                "_fetch_multiget_chunk",
                new=AsyncMock(
                    side_effect=[
                        ([{"seller_id": 1, "mlc": "MLC1"}], 0),
                        ([{"seller_id": 1, "mlc": "MLC2"}], 0),
                    ]
                ),
            ),
            patch.object(
                supabase_publications_store,
                "upsert_rows",
                new=AsyncMock(side_effect=lambda rows: len(rows)),
            ),
            patch.object(
                supabase_publications_store,
                "count_by_sync_run",
                new=AsyncMock(return_value=1),
            ),
            patch.object(
                supabase_publications_store,
                "delete_stale_rows",
                new=AsyncMock(),
            ) as delete_stale_rows,
            patch.object(publication_sync_store, "update", new=Mock()),
            patch.object(publication_sync_store, "finish", new=Mock()) as finish,
        ):
            summary = await service.sync_publications(user_id="1")

        delete_stale_rows.assert_not_awaited()
        self.assertEqual(summary["verified_count"], 1)
        self.assertEqual(finish.call_args.kwargs["status"], "partial")

    async def test_stale_rows_are_deleted_only_after_exact_verification(self):
        service = PublicationSyncService()
        service.MULTIGET_CHUNK_SIZE = 1
        service.MULTIGET_CONCURRENCY = 2

        with (
            patch.object(
                supabase_publications_store,
                "ensure_ready",
                new=AsyncMock(),
            ),
            patch.object(
                service,
                "_resolve_seller_id",
                new=AsyncMock(return_value="1"),
            ),
            patch.object(
                service,
                "_scan_all_item_ids",
                new=AsyncMock(return_value=["MLC1", "MLC2"]),
            ),
            patch.object(
                service,
                "_fetch_multiget_chunk",
                new=AsyncMock(
                    side_effect=[
                        ([{"seller_id": 1, "mlc": "MLC1"}], 0),
                        ([{"seller_id": 1, "mlc": "MLC2"}], 0),
                    ]
                ),
            ),
            patch.object(
                supabase_publications_store,
                "upsert_rows",
                new=AsyncMock(side_effect=lambda rows: len(rows)),
            ),
            patch.object(
                supabase_publications_store,
                "count_by_sync_run",
                new=AsyncMock(return_value=2),
            ),
            patch.object(
                supabase_publications_store,
                "delete_stale_rows",
                new=AsyncMock(),
            ) as delete_stale_rows,
            patch.object(publication_sync_store, "update", new=Mock()),
            patch.object(publication_sync_store, "finish", new=Mock()) as finish,
        ):
            summary = await service.sync_publications(user_id="1")

        delete_stale_rows.assert_awaited_once()
        self.assertEqual(summary["verified_count"], 2)
        self.assertEqual(finish.call_args.kwargs["status"], "success")


if __name__ == "__main__":
    unittest.main()
