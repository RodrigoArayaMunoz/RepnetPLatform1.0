import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from openpyxl import load_workbook

from celery_app import celery_app
from config import settings
from services.job_store import JobStore
from services.ml_client import MercadoLibreClient, ml_client
from services.publication_export_service import PublicationExportService
from services.supabase_publications_store import supabase_publications_store
from routers import publications_router


class _FakeRedisClient:
    async def aclose(self):
        return None


class _FakeLimiter:
    def __init__(self, *args, **kwargs):
        self.client = _FakeRedisClient()
        self.acquire_count = 0
        self.penalties = []

    async def acquire(self):
        self.acquire_count += 1
        return None

    async def penalize(self, cooldown_seconds):
        self.penalties.append(cooldown_seconds)


class _FakeDescriptionCache:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get(self, item_id):
        return self.values.get(item_id)

    async def set(self, item_id, *, description, result):
        self.values[item_id] = (description, result)

    async def close(self):
        return None


class PublicationDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_description_uses_official_item_resource_and_plain_text(self):
        service = PublicationExportService()
        limiter = _FakeLimiter()

        with patch.object(
            ml_client,
            "request",
            new=AsyncMock(
                return_value={
                    "plain_text": "Descripcion oficial del producto",
                    "text": "Texto alternativo",
                }
            ),
        ) as request:
            description, result = await service._fetch_description(
                item_id="MLC123456",
                user_id="2682261950",
                limiter=limiter,
            )

        self.assertEqual(result, "found")
        self.assertEqual(description, "Descripcion oficial del producto")
        request.assert_awaited_once_with(
            "GET",
            "/items/MLC123456/description",
            user_id="2682261950",
            rate_limiter=limiter,
        )

    async def test_429_pauses_shared_limiter_and_respects_retry_after(self):
        client = MercadoLibreClient()
        client.client = Mock()
        client.client.request = AsyncMock(
            side_effect=[
                httpx.Response(
                    429,
                    headers={"Retry-After": "120"},
                    json={"message": "too_many_requests"},
                    request=httpx.Request(
                        "GET",
                        "https://api.mercadolibre.com/items/MLC1/description",
                    ),
                ),
                httpx.Response(
                    200,
                    json={"plain_text": "Descripcion"},
                    request=httpx.Request(
                        "GET",
                        "https://api.mercadolibre.com/items/MLC1/description",
                    ),
                ),
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
            patch(
                "services.ml_client.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
            patch("services.ml_client.random.uniform", return_value=0),
        ):
            payload = await client.request(
                "GET",
                "/items/MLC1/description",
                user_id="2682261950",
                rate_limiter=limiter,
            )

        self.assertEqual(payload["plain_text"], "Descripcion")
        self.assertEqual(limiter.acquire_count, 2)
        self.assertEqual(limiter.penalties, [120.0])
        sleep.assert_awaited_once_with(120.0)


class PublicationExcelTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_writes_required_columns_without_updating_supabase(self):
        service = PublicationExportService()
        publications = [
            {
                "mlc": "MLC123",
                "sku": "SKU-1",
                "titulo": "Titulo 1",
            },
            {
                "mlc": "MLC456",
                "sku": "SKU-2",
                "titulo": "Titulo 2",
            },
        ]
        cache = _FakeDescriptionCache()

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(
                    supabase_publications_store,
                    "list_by_creation_date",
                    new=AsyncMock(return_value=publications),
                ),
                patch.object(
                    supabase_publications_store,
                    "upsert_rows",
                    new=AsyncMock(),
                ) as upsert_rows,
                patch.object(
                    service,
                    "_fetch_description",
                    new=AsyncMock(
                        side_effect=[
                            ("Descripcion 1", "found"),
                            ("Descripcion 2", "found"),
                        ]
                    ),
                ),
                patch(
                    "services.publication_export_service."
                    "RedisWindowRateLimiter",
                    _FakeLimiter,
                ),
                patch(
                    "services.publication_export_service."
                    "PublicationDescriptionCache",
                    return_value=cache,
                ),
                patch.object(JobStore, "update", new=Mock()),
            ):
                summary = await service.export(
                    job_id="job-1",
                    user_id="2682261950",
                    creation_date="2026-07-24",
                )

            output_path = os.path.join(
                temp_dir,
                "job-1_publicaciones_2026-07-24.xlsx",
            )
            workbook = load_workbook(output_path, read_only=True)
            worksheet = workbook["Publicaciones"]
            rows = list(worksheet.iter_rows(values_only=True))
            workbook.close()

        self.assertEqual(
            rows[0],
            ("MLC", "SKU", "TITULO", "DESCRIPCION"),
        )
        self.assertEqual(
            rows[1],
            ("MLC123", "SKU-1", "Titulo 1", "Descripcion 1"),
        )
        self.assertEqual(
            rows[2],
            ("MLC456", "SKU-2", "Titulo 2", "Descripcion 2"),
        )
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["descriptions_found"], 2)
        self.assertEqual(summary["api_items_queried"], 2)
        upsert_rows.assert_not_awaited()

    async def test_export_uses_bounded_concurrency(self):
        service = PublicationExportService()
        publications = [
            {
                "mlc": f"MLC{index}",
                "sku": f"SKU-{index}",
                "titulo": f"Titulo {index}",
            }
            for index in range(12)
        ]
        active_requests = 0
        max_active_requests = 0

        async def fetch_description(**kwargs):
            nonlocal active_requests, max_active_requests
            active_requests += 1
            max_active_requests = max(
                max_active_requests,
                active_requests,
            )
            await asyncio.sleep(0.01)
            active_requests -= 1
            return f"Descripcion {kwargs['item_id']}", "found"

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(
                    settings,
                    "ml_publication_export_concurrency",
                    3,
                ),
                patch.object(
                    settings,
                    "ml_publication_export_batch_size",
                    50,
                ),
                patch.object(
                    supabase_publications_store,
                    "list_by_creation_date",
                    new=AsyncMock(return_value=publications),
                ),
                patch.object(
                    service,
                    "_fetch_description",
                    new=AsyncMock(side_effect=fetch_description),
                ),
                patch(
                    "services.publication_export_service."
                    "RedisWindowRateLimiter",
                    _FakeLimiter,
                ),
                patch(
                    "services.publication_export_service."
                    "PublicationDescriptionCache",
                    return_value=_FakeDescriptionCache(),
                ),
                patch.object(JobStore, "update", new=Mock()),
            ):
                await service.export(
                    job_id="job-concurrency",
                    user_id="2682261950",
                    creation_date="2026-07-24",
                )

        self.assertGreater(max_active_requests, 1)
        self.assertLessEqual(max_active_requests, 3)

    async def test_cached_descriptions_skip_mercado_libre(self):
        service = PublicationExportService()
        publications = [
            {
                "mlc": "MLC-CACHED",
                "sku": "SKU-CACHED",
                "titulo": "Titulo cache",
            }
        ]
        fetch_description = AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(
                    supabase_publications_store,
                    "list_by_creation_date",
                    new=AsyncMock(return_value=publications),
                ),
                patch.object(
                    service,
                    "_fetch_description",
                    new=fetch_description,
                ),
                patch(
                    "services.publication_export_service."
                    "RedisWindowRateLimiter",
                    _FakeLimiter,
                ),
                patch(
                    "services.publication_export_service."
                    "PublicationDescriptionCache",
                    return_value=_FakeDescriptionCache(
                        {
                            "MLC-CACHED": (
                                "Descripcion desde cache",
                                "found",
                            )
                        }
                    ),
                ),
                patch.object(JobStore, "update", new=Mock()),
            ):
                summary = await service.export(
                    job_id="job-cache",
                    user_id="2682261950",
                    creation_date="2026-07-24",
                )

        fetch_description.assert_not_awaited()
        self.assertEqual(summary["cache_hits"], 1)
        self.assertEqual(summary["api_items_queried"], 0)

    async def test_export_resumes_from_last_complete_checkpoint(self):
        service = PublicationExportService()
        publications = [
            {
                "mlc": f"MLC{index}",
                "sku": f"SKU-{index}",
                "titulo": f"Titulo {index}",
            }
            for index in range(4)
        ]

        async def fail_second_batch(*, item_id, **kwargs):
            if item_id in {"MLC2", "MLC3"}:
                raise HTTPException(status_code=502, detail="network")
            return f"Descripcion {item_id}", "found"

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = _FakeDescriptionCache()
            common_patches = (
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(
                    settings,
                    "ml_publication_export_batch_size",
                    2,
                ),
                patch.object(
                    settings,
                    "ml_publication_export_concurrency",
                    2,
                ),
                patch.object(
                    supabase_publications_store,
                    "list_by_creation_date",
                    new=AsyncMock(return_value=publications),
                ),
                patch(
                    "services.publication_export_service."
                    "RedisWindowRateLimiter",
                    _FakeLimiter,
                ),
                patch(
                    "services.publication_export_service."
                    "PublicationDescriptionCache",
                    return_value=cache,
                ),
                patch.object(JobStore, "update", new=Mock()),
            )

            with (
                common_patches[0],
                common_patches[1],
                common_patches[2],
                common_patches[3],
                common_patches[4],
                common_patches[5],
                common_patches[6],
                patch.object(
                    service,
                    "_fetch_description",
                    new=AsyncMock(side_effect=fail_second_batch),
                ),
            ):
                with self.assertRaises(HTTPException):
                    await service.export(
                        job_id="job-resume",
                        user_id="2682261950",
                        creation_date="2026-07-24",
                    )

            checkpoint_path = os.path.join(
                temp_dir,
                (
                    "job-resume_publicaciones_2026-07-24."
                    "checkpoint.jsonl"
                ),
            )
            with open(checkpoint_path, "ab") as checkpoint_file:
                checkpoint_file.write(b'{"mlc":"incomplete')

            resumed_fetch = AsyncMock(
                side_effect=[
                    ("Descripcion MLC2", "found"),
                    ("Descripcion MLC3", "found"),
                ]
            )
            with (
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(
                    settings,
                    "ml_publication_export_batch_size",
                    2,
                ),
                patch.object(
                    settings,
                    "ml_publication_export_concurrency",
                    2,
                ),
                patch.object(
                    supabase_publications_store,
                    "list_by_creation_date",
                    new=AsyncMock(return_value=publications),
                ),
                patch(
                    "services.publication_export_service."
                    "RedisWindowRateLimiter",
                    _FakeLimiter,
                ),
                patch(
                    "services.publication_export_service."
                    "PublicationDescriptionCache",
                    return_value=cache,
                ),
                patch.object(JobStore, "update", new=Mock()),
                patch.object(
                    service,
                    "_fetch_description",
                    new=resumed_fetch,
                ),
            ):
                summary = await service.export(
                    job_id="job-resume",
                    user_id="2682261950",
                    creation_date="2026-07-24",
                )

            output_path = os.path.join(
                temp_dir,
                "job-resume_publicaciones_2026-07-24.xlsx",
            )
            workbook = load_workbook(output_path, read_only=True)
            rows = list(
                workbook["Publicaciones"].iter_rows(values_only=True)
            )
            workbook.close()

        self.assertEqual(resumed_fetch.await_count, 2)
        self.assertTrue(summary["resumed_from_checkpoint"])
        self.assertEqual(len(rows), 5)

    def test_export_task_uses_dedicated_export_queue(self):
        routes = celery_app.conf.task_routes
        self.assertEqual(
            routes["tasks.export_publications_job"]["queue"],
            "publication_exports",
        )

    def test_safe_export_defaults(self):
        self.assertEqual(
            settings.ml_publication_export_requests_per_second,
            2.0,
        )
        self.assertEqual(settings.ml_publication_export_concurrency, 5)
        self.assertEqual(settings.ml_publication_export_batch_size, 50)


class PublicationExportRecoveryTests(unittest.TestCase):
    def test_stale_processing_job_is_requeued_from_checkpoint(self):
        job = {
            "id": "job-stale",
            "status": "processing",
            "heartbeat_at": 100.0,
            "ml_user_id": "2682261950",
            "publication_date": "2026-06-17",
            "recovery_count": 0,
        }
        recovered_job = {
            **job,
            "status": "queued",
            "recovery_count": 1,
        }
        async_result = Mock(id="task-recovery")

        with (
            patch(
                "routers.publications_router.time.time",
                return_value=500.0,
            ),
            patch.object(
                settings,
                "ml_publication_export_recovery_stale_seconds",
                180,
            ),
            patch.object(
                publications_router.publication_export_store,
                "has_run_lock",
                return_value=False,
            ),
            patch.object(
                publications_router.publication_export_store,
                "try_acquire_recovery_guard",
                return_value=True,
            ),
            patch.object(
                publications_router.export_publications_task,
                "delay",
                return_value=async_result,
            ) as delay,
            patch.object(JobStore, "update", new=Mock()) as update,
            patch.object(
                JobStore,
                "get",
                return_value=recovered_job,
            ),
        ):
            result = (
                publications_router._recover_stale_export_if_needed(job)
            )

        delay.assert_called_once_with(
            "job-stale",
            "2682261950",
            "2026-06-17",
        )
        self.assertEqual(
            update.call_args.kwargs["recovery_count"],
            1,
        )
        self.assertEqual(result["status"], "queued")

    def test_active_run_lock_prevents_duplicate_recovery(self):
        job = {
            "id": "job-active",
            "status": "processing",
            "heartbeat_at": 100.0,
        }

        with (
            patch(
                "routers.publications_router.time.time",
                return_value=500.0,
            ),
            patch.object(
                settings,
                "ml_publication_export_recovery_stale_seconds",
                180,
            ),
            patch.object(
                publications_router.publication_export_store,
                "has_run_lock",
                return_value=True,
            ),
            patch.object(
                publications_router.export_publications_task,
                "delay",
            ) as delay,
        ):
            result = (
                publications_router._recover_stale_export_if_needed(job)
            )

        delay.assert_not_called()
        self.assertIs(result, job)


if __name__ == "__main__":
    unittest.main()
