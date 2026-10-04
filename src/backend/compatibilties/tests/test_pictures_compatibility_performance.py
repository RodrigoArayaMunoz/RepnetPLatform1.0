import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from openpyxl import Workbook
from pydantic import ValidationError

from config import Settings, settings
from scripts.check_price_stock_config import checked_profile
from services.compatibility_batch_service import (
    CompatibilityBatchState, build_compat_summary,
    post_compatibility_families_batch, process_compatibility_batches,
)
from services.compatibility_service import (
    JobCaches, JobMetrics, call_ml, count_vehicle_family_products,
    process_rows_for_job,
    COMPATIBILITY_RATE_LIMITER, COMPATIBILITY_WRITE_RATE_LIMITER,
    ITEM_PICTURES_RATE_LIMITER, ITEM_PICTURES_WRITE_RATE_LIMITER, WRITE_RATE_LIMITER,
)
from services.item_pictures_service import (
    _process_item_picture_row, load_item_picture_rows, process_item_pictures_job,
)
from services.ml_client import MercadoLibreClient, _parse_retry_after_seconds
from services.excel_service import load_excel_rows


def limiter_mock():
    limiter = Mock()
    limiter.window_seconds = 60
    limiter.acquire = AsyncMock()
    limiter.penalize = AsyncMock()
    return limiter


def family_row(item_id="MLC123", key="a"):
    attributes = [
        {"id": name, "value_id": str(index)}
        for index, name in enumerate(("BRAND", "CAR_AND_VAN_MODEL", "YEAR",
                                     "CAR_AND_VAN_SUBMODEL", "CAR_AND_VAN_ENGINE",
                                     "TRANSMISSION_CONTROL_TYPE"), start=1)
    ]
    return {"ok": True, "item_id": item_id, "product_family_key": key,
            "product_family": {"domain_id": settings.ml_domain_id, "attributes": attributes},
            "family_product_count": 1, "category_id": "MLC1748", "user_product_id": "UP" + item_id}


class ManagedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, method, kwargs, outcomes):
        client = MercadoLibreClient()
        client.get_valid_token = AsyncMock(return_value="test-token")
        calls = []

        def respond(request):
            calls.append(request)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            code, body, headers = outcome
            return httpx.Response(code, json=body, headers=headers)

        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        limiter = limiter_mock()
        metrics = JobMetrics()
        try:
            with (patch.object(settings, "ml_retry_attempts", 3),
                  patch("services.ml_client.asyncio.sleep", new=AsyncMock()) as sleep,
                  patch("services.ml_client.random.uniform", return_value=0)):
                try:
                    result = await call_ml(getattr(client, method), access_token="test-token",
                                           user_id="99", metrics=metrics, limiter=limiter,
                                           client_managed_retry=True, **kwargs)
                except HTTPException as exc:
                    result = exc
        finally:
            await client.client.aclose()
        return result, metrics, limiter, calls, sleep

    def operations(self):
        return (
            ("update_item_pictures", {"item_id": "MLC123", "picture_urls": ["https://images.test/a.jpg"]}),
            ("add_user_product_compatibilities_batch", {"user_product_id": "UP1", "category_id": "MLC1", "product_ids": ["P1"]}),
            ("add_user_product_compatibility_families_batch", {"user_product_id": "UP1", "category_id": "MLC1", "product_families": [{}]}),
            ("update_user_product_compatibility_families_batch", {"user_product_id": "UP1", "category_id": "MLC1", "product_families": [{}]}),
            ("add_item_compatibility_exception", {"item_id": "MLC123", "comment": "Sin datos"}),
        )

    async def test_all_writers_account_for_each_attempt_and_respect_retry_after(self):
        for method, kwargs in self.operations():
            with self.subTest(method=method):
                result, metrics, limiter, calls, sleep = await self.run_request(method, kwargs, [
                    (429, {"message": "limited"}, {"Retry-After": "90"}),
                    httpx.ReadTimeout("timeout"), (200, {"created_compatibilities_count": 1}, {}),
                ])
                self.assertNotIsInstance(result, HTTPException)
                self.assertEqual(metrics.ml_requests, 3)
                self.assertEqual(metrics.ml_retries, 2)
                self.assertEqual(metrics.ml_rate_limited, 1)
                self.assertEqual(limiter.acquire.await_count, 3)
                limiter.penalize.assert_awaited_with(90)
                self.assertEqual(sleep.await_args_list[0].args[0], 90)
                expected_timeout = (settings.ml_item_pictures_http_timeout_seconds
                                    if method == "update_item_pictures"
                                    else settings.ml_compatibility_http_timeout_seconds)
                self.assertEqual(calls[0].extensions["timeout"]["read"], expected_timeout)

    async def test_retry_exhaustion_does_not_restart_an_outer_loop(self):
        for method, kwargs in self.operations():
            with self.subTest(method=method):
                result, metrics, limiter, calls, _ = await self.run_request(method, kwargs, [
                    (429, {}, {}), (429, {}, {}), (429, {}, {}),
                ])
                self.assertIsInstance(result, HTTPException)
                self.assertEqual(result.status_code, 429)
                self.assertEqual(len(calls), 3)
                self.assertEqual(limiter.acquire.await_count, 3)
                self.assertEqual(metrics.ml_rate_limited, 3)
                self.assertEqual(metrics.ml_http_errors, 1)
                limiter.penalize.assert_awaited_with(max(60, settings.ml_retry_429_cooldown_seconds))

    async def test_reads_and_catalog_counts_limit_each_retry(self):
        for method, kwargs in (
            ("get_item_detail", {"item_id": "MLC123"}),
            ("get_top_values", {"attribute_id": "BRAND"}),
            ("count_vehicle_family_products", {"attributes": []}),
        ):
            with self.subTest(method=method):
                result, metrics, limiter, calls, _ = await self.run_request(method, kwargs, [
                    (503, {}, {}), (200, {"count": 1}, {}),
                ])
                self.assertNotIsInstance(result, HTTPException)
                self.assertEqual(metrics.ml_requests, 2)
                self.assertEqual(limiter.acquire.await_count, len(calls))

    async def test_business_errors_are_not_retried(self):
        result, metrics, limiter, calls, _ = await self.run_request(
            *self.operations()[0], [(400, {"message": "invalid picture"}, {})])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(metrics.ml_retries, 0)
        self.assertEqual(limiter.acquire.await_count, 1)


class PictureFileTests(unittest.IsolatedAsyncioTestCase):
    def make_file(self, directory, rows):
        path = Path(directory, "pictures.xlsx")
        workbook = Workbook()
        workbook.active.title = "Hoja1"
        workbook.active.append([" MLC ", "URLS", "Foto 1"])
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
        return path

    async def test_identical_duplicate_rows_write_once_and_preserve_every_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_file(directory, [
                ["MLC123", "https://images.test/a.jpg|||https://images.test/b.jpg", "https://images.test/a.jpg"],
                ["MLC123", "https://images.test/a.jpg|||https://images.test/b.jpg", None],
            ])
            with (patch("services.item_pictures_service.ml_client.get_valid_token", new=AsyncMock(return_value="token")),
                  patch("services.item_pictures_service.call_ml", new=AsyncMock(return_value={})) as write,
                  patch("services.item_pictures_service.JobStore.update"),
                  patch.object(settings, "upload_dir", directory)):
                outcome = await process_item_pictures_job("job", "99", str(path))
        self.assertEqual(write.await_count, 1)
        self.assertTrue(write.await_args.kwargs["client_managed_retry"])
        self.assertEqual(len(write.await_args.kwargs["picture_urls"]), 2)
        self.assertEqual([row["original_row_index"] for row in outcome["results"]], [0, 1])
        self.assertEqual(outcome["summary"]["success_count"], 2)
        self.assertEqual(outcome["summary"]["updated_items"], 1)

    async def test_conflicting_duplicates_and_invalid_urls_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_file(directory, [["MLC123", "https://images.test/a.jpg", None],
                                              ["MLC123", "https://images.test/b.jpg", None],
                                              ["MLC124", "file:///a.jpg", None]])
            rows = load_item_picture_rows(str(path))
            with patch("services.item_pictures_service.call_ml", new=AsyncMock()) as write:
                results = [await _process_item_picture_row(access_token="token", row=row,
                                                          user_id="99", metrics=JobMetrics()) for row in rows]
        self.assertTrue(all(not row["ok"] for row in results))
        write.assert_not_awaited()

    async def test_blocks_preserve_order_and_continue_after_a_row_error(self):
        rows = [{"item_id": f"MLC{i}", "original_row_index": i} for i in range(7)]
        active = 0
        maximum = 0
        real_sleep = asyncio.sleep

        async def process(**kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await real_sleep(0)
            active -= 1
            row = kwargs["row"]
            return {**row, "ok": row["original_row_index"] != 2}

        with tempfile.TemporaryDirectory() as directory:
            with (patch("services.item_pictures_service.load_item_picture_rows", return_value=rows),
                  patch("services.item_pictures_service._process_item_picture_row", side_effect=process),
                  patch("services.item_pictures_service.ml_client.get_valid_token", new=AsyncMock(return_value="token")),
                  patch("services.item_pictures_service.JobStore.update") as progress,
                  patch("services.item_pictures_service.asyncio.sleep", new=AsyncMock()) as sleep,
                  patch.object(settings, "upload_dir", directory),
                  patch.object(settings, "item_pictures_chunk_size", 3),
                  patch.object(settings, "item_pictures_chunk_pause_seconds", 0),
                  patch.object(settings, "item_pictures_max_concurrency", 2),
                  patch.object(settings, "job_progress_update_every", 3)):
                outcome = await process_item_pictures_job("job", "99", "unused.xlsx")
        self.assertEqual(maximum, 2)
        self.assertEqual([row["original_row_index"] for row in outcome["results"]], list(range(7)))
        self.assertEqual(outcome["summary"]["success_count"], 6)
        self.assertEqual([call.kwargs["processed_rows"] for call in progress.call_args_list
                          if "processed_rows" in call.kwargs], [3, 6, 7, 7])
        sleep.assert_not_awaited()


class CompatibilityCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_resolved_family_is_rejected_before_any_write(self):
        with patch("services.compatibility_batch_service.call_ml", new=AsyncMock()) as write:
            outcome = await process_compatibility_batches(
                access_token="token", user_id="99", rows=[{**family_row(), "family_product_count": 201}],
            )
        write.assert_not_awaited()
        self.assertFalse(outcome["results"][0]["ok"])
        self.assertEqual(outcome["results"][0]["error_code"], "PRODUCT_FAMILY_LIMIT_EXCEEDED")

    async def test_resolution_is_reused_across_chunks_and_preserves_original_restrictions(self):
        caches = JobCaches()
        raw = {"ASOCIACION ML": "MLC123", "MARCA": "Marca", "MODELO": "Modelo",
               "VERSION": "Version", "CILINDRADA": "2.0", "TRANSMISION": "Manual", "AÑO": 2020}
        with patch("services.compatibility_service.resolve_vehicle_product_row", new=AsyncMock(return_value=family_row())) as resolve:
            for position in ("Izquierda", "Derecha"):
                outcome = await process_rows_for_job(
                    "job", "token", "99", [{**raw, "IZQUIERDA/DERECHA": position}],
                    catalog_cache=Mock(), caches=caches, manage_job_updates=False,
                )
                self.assertEqual(outcome["results"][0]["posicion_id"], position)
        self.assertEqual(resolve.await_count, 1)

    async def test_batches_serialize_each_target_and_allow_other_items_to_progress(self):
        rows = [family_row("MLC1", str(index)) for index in range(21)]
        rows.append(family_row("MLC2"))
        active = set()
        maximum = 0

        async def post(**kwargs):
            nonlocal maximum
            item_id = kwargs["item_id"]
            self.assertNotIn(item_id, active)
            active.add(item_id)
            maximum = max(maximum, len(active))
            await asyncio.sleep(0)
            active.remove(item_id)
            return {"ok": True, "item_id": item_id, "created_compatibilities_count": 1,
                    "compatibility_keys": [entry["compatibility_key"] for entry in kwargs["family_entries"]]}

        with (patch("services.compatibility_batch_service.post_compatibility_families_batch", side_effect=post),
              patch.object(settings, "compat_batch_concurrency", 2),
              patch.object(settings, "job_progress_update_every", 2)):
            progress = AsyncMock()
            outcome = await process_compatibility_batches(access_token="token", user_id="99", rows=rows,
                                                         on_progress=progress)
        self.assertEqual(maximum, 2)
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertEqual([call.args[0] for call in progress.await_args_list], [2, 4])

    async def test_successful_families_are_not_written_again_across_file_chunks(self):
        state = CompatibilityBatchState()
        with patch("services.compatibility_batch_service.call_ml", new=AsyncMock(return_value={"created_compatibilities_count": 2})) as write:
            first = await process_compatibility_batches(access_token="token", user_id="99",
                                                       rows=[family_row()], state=state)
            second = await process_compatibility_batches(access_token="token", user_id="99",
                                                        rows=[{**family_row(), "original_row_index": 301}], state=state)
        self.assertEqual(write.await_count, 2)  # POST and required note PUT
        self.assertTrue(second["results"][0]["ok"])
        self.assertEqual(second["results"][0]["original_row_index"], 301)
        self.assertEqual(first["summary"]["total_created_compatibilities"], 2)
        self.assertEqual(second["summary"]["total_created_compatibilities"], 0)

    async def test_same_vehicle_count_is_fetched_once_under_concurrency(self):
        caches = JobCaches()
        metrics = JobMetrics()
        with patch("services.compatibility_service.call_ml", new=AsyncMock(return_value=7)) as count:
            results = await asyncio.gather(*(count_vehicle_family_products(
                "token", "99", "1", "2", "3", "4", "5", "6", caches, metrics,
            ) for _ in range(4)))
        self.assertEqual(count.await_count, 1)
        self.assertEqual([count for count, _ in results], [7] * 4)

    async def test_note_failure_preserves_confirmed_created_count(self):
        row = family_row()
        entry = {"compatibility_key": "family:a", "matched_products_count": 1,
                 "payload": {**row["product_family"], "note": "Nota"}}
        with patch("services.compatibility_batch_service.call_ml", new=AsyncMock(side_effect=[
            {"created_compatibilities_count": 3}, HTTPException(status_code=503, detail="Unavailable"),
        ])):
            result = await post_compatibility_families_batch(
                access_token="token", user_id="99", item_id="MLC123", family_entries=[entry],
                metrics=JobMetrics(), item_compact=row,
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["creation_committed"])
        self.assertEqual(build_compat_summary([], [result], JobMetrics())["total_created_compatibilities"], 3)


class PerformanceProfileTests(unittest.TestCase):
    def test_retry_after_supports_http_dates_and_invalid_headers_use_reset_fallback(self):
        with patch("services.ml_client.time.time", return_value=0):
            self.assertEqual(_parse_retry_after_seconds(httpx.Response(429, headers={
                "Retry-After": "Thu, 01 Jan 1970 00:01:30 GMT",
            })), 90)
            self.assertEqual(_parse_retry_after_seconds(httpx.Response(429, headers={
                "Retry-After": "invalid", "X-RateLimit-Reset": "120",
            })), 120)
            self.assertIsNone(_parse_retry_after_seconds(httpx.Response(429, headers={"Retry-After": "inf"})))

    def test_compose_profiles_override_legacy_settings_for_all_backend_services(self):
        root = Path(__file__).resolve().parents[4]
        for relative in ("deploy/docker-compose.prod.yml", "src/backend/compatibilties/docker-compose.yml"):
            text = (root / relative).read_text(encoding="utf-8")
            for key, default in (("ITEM_PICTURES_CHUNK_SIZE", 300), ("COMPATIBILITY_CHUNK_SIZE", 300),
                                 ("ITEM_PICTURES_CHUNK_PAUSE_SECONDS", 0), ("COMPATIBILITY_CHUNK_PAUSE_SECONDS", 0),
                                 ("ML_ITEM_PICTURES_MAX_REQUESTS_PER_WINDOW", 100),
                                 ("ML_COMPATIBILITY_MAX_REQUESTS_PER_WINDOW", 100)):
                self.assertIn(f"{key}: ${{{key}:-{default}}}", text)
            self.assertEqual(text.count("*ml-performance-environment"), 6)

    def test_compatibility_reader_keeps_aliases_empty_cells_and_falls_back_to_first_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "compatibilities.xlsx")
            workbook = Workbook()
            workbook.active.title = "Aplicaciones"
            workbook.active.append([" ASOCIACIÓN ML ", "MARCA", "MODELO", "VERSIÓN",
                                    "CILINDRADA", "TRANSMISIÓN", "ANO", "IZQUIERDA/DERECHA"])
            workbook.active.append(["MLC123", "Marca", "Modelo", "Versión", "2.0", "Manual", 2020, None])
            workbook.save(path)
            workbook.close()
            rows = load_excel_rows(str(path))
        self.assertEqual(rows[0]["ASOCIACION ML"], "MLC123")
        self.assertIsNone(rows[0]["POSICION_ID"])
        self.assertEqual(rows[0]["AÑO"], 2020)

    def test_endpoint_budgets_also_acquire_shared_global_budget(self):
        self.assertEqual(ITEM_PICTURES_WRITE_RATE_LIMITER.limiters, (ITEM_PICTURES_RATE_LIMITER, WRITE_RATE_LIMITER))
        self.assertEqual(COMPATIBILITY_WRITE_RATE_LIMITER.limiters, (COMPATIBILITY_RATE_LIMITER, WRITE_RATE_LIMITER))
        profile = checked_profile()
        self.assertEqual(profile["pictures_http_timeout_seconds"], settings.ml_item_pictures_http_timeout_seconds)

    def test_mismatched_endpoint_configuration_is_rejected(self):
        with patch.object(settings, "ml_item_pictures_max_requests_per_window", settings.ml_item_pictures_max_requests_per_window + 1):
            with self.assertRaisesRegex(RuntimeError, "pictures"):
                checked_profile()

    def test_invalid_limits_concurrency_and_timeouts_are_rejected(self):
        for key in ("ml_item_pictures_requests_per_second", "ml_compatibility_max_requests_per_window",
                    "item_pictures_max_concurrency", "compatibility_chunk_size",
                    "ml_item_pictures_http_timeout_seconds", "ml_compatibility_http_timeout_seconds"):
            with self.subTest(setting=key), self.assertRaises(ValidationError):
                Settings(_env_file=None, **{key: 0})


if __name__ == "__main__":
    unittest.main()
