import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pydantic import ValidationError

from config import Settings, settings
from scripts.check_price_stock_config import checked_profile
from services.compatibility_service import JobMetrics
from services.ml_client import MercadoLibreClient
from services.price_stock_service import _process_price_stock_row, process_price_stock_job
from services.redis_rate_limiter import RedisWindowRateLimiter


class PriceStockRequestTests(unittest.IsolatedAsyncioTestCase):
    async def run_row(self, outcomes, attempts=3):
        client = MercadoLibreClient()
        client.get_valid_token = AsyncMock(return_value="test-token")
        calls = []

        def respond(request):
            calls.append(request)
            result = outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            code, body, headers = result
            return httpx.Response(code, json=body, headers=headers)

        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        limiter = Mock()
        limiter.window_seconds = 60
        limiter.acquire = AsyncMock()
        limiter.penalize = AsyncMock()
        metrics = JobMetrics()
        row = {"item_id": "MLC123", "precio": 12300, "stock": 0,
               "estado": "paused", "estado_raw": "PAUSADO", "original_row_index": 0}
        try:
            with (
                patch("services.price_stock_service.ml_client", client),
                patch("services.price_stock_service.PRICE_STOCK_WRITE_RATE_LIMITER", limiter),
                patch.object(settings, "ml_retry_attempts", attempts),
                patch("services.ml_client.asyncio.sleep", new=AsyncMock()) as sleep,
                patch("services.ml_client.random.uniform", return_value=0),
            ):
                result = await _process_price_stock_row(row, "99", metrics)
        finally:
            await client.client.aclose()
        return result, metrics, limiter, calls, sleep, client

    async def test_every_429_retry_acquires_shared_limit_and_is_counted(self):
        success = {"id": "MLC123", "price": 12300, "available_quantity": 0, "status": "paused"}
        result, metrics, limiter, calls, sleep, client = await self.run_row([
            (429, {"message": "rate limit"}, {"Retry-After": "120"}),
            (429, {"message": "rate limit"}, {"Retry-After": "120"}),
            (200, success, {}),
        ])
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(limiter.acquire.await_count, 3)
        self.assertEqual(metrics.ml_requests, 3)
        self.assertEqual(metrics.ml_retries, 2)
        self.assertEqual(metrics.ml_rate_limited, 2)
        self.assertEqual(metrics.ml_http_errors, 0)
        self.assertEqual(sleep.await_args_list[0].args[0], 120)
        limiter.penalize.assert_awaited_with(120)
        client.get_valid_token.assert_awaited_once_with("99")
        self.assertIn(b'"available_quantity":0', calls[0].content)

    async def test_exhausted_retry_budget_does_not_multiply_into_another_retry_loop(self):
        result, metrics, limiter, calls, _, _ = await self.run_row([
            (429, {"message": "rate limit"}, {}),
            (429, {"message": "rate limit"}, {}),
            (429, {"message": "rate limit"}, {}),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "HTTP_429")
        self.assertEqual(len(calls), 3)
        self.assertEqual(limiter.acquire.await_count, 3)
        self.assertEqual(metrics.ml_requests, 3)
        self.assertEqual(metrics.ml_rate_limited, 3)
        self.assertEqual(metrics.ml_http_errors, 1)
        limiter.penalize.assert_awaited_with(60)

    async def test_timeouts_are_counted_and_retried_within_the_same_budget(self):
        result, metrics, limiter, calls, _, _ = await self.run_row([
            httpx.ReadTimeout("test timeout"),
            httpx.ReadTimeout("test timeout"),
            httpx.ReadTimeout("test timeout"),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "HTTP_502")
        self.assertEqual(metrics.ml_requests, 3)
        self.assertEqual(metrics.ml_retries, 2)
        self.assertEqual(metrics.ml_technical_errors, 1)
        self.assertEqual(limiter.acquire.await_count, len(calls))

    async def test_invalid_row_never_spends_api_budget(self):
        with patch("services.price_stock_service.ml_client.update_item_price_stock", new=AsyncMock()) as update:
            result = await _process_price_stock_row({"item_id": None}, "99", JobMetrics())
        self.assertFalse(result["ok"])
        update.assert_not_awaited()

    async def test_http_200_with_ignored_price_is_reported_as_not_confirmed(self):
        result, metrics, _, _, _, _ = await self.run_row([
            (200, {"id": "MLC123", "price": 9900, "available_quantity": 0,
                   "status": "paused", "warnings": [{"code": "price_ignored"}]}, {}),
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "UPDATE_NOT_CONFIRMED")
        self.assertEqual(result["mismatched_fields"], ["price"])
        self.assertEqual(metrics.ml_requests, 1)


class PriceStockBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_fixed_pause_and_original_result_order_across_blocks(self):
        rows = [{"item_id": f"MLC{i}", "original_row_index": i} for i in range(7)]
        active = 0
        maximum = 0

        async def update(row, user_id, metrics):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            return {"ok": True, "item_id": row["item_id"], "original_row_index": row["original_row_index"]}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("services.price_stock_service.load_price_stock_rows", return_value=rows),
                patch("services.price_stock_service._process_price_stock_row", side_effect=update),
                patch("services.price_stock_service.JobStore.update") as progress,
                patch.object(settings, "upload_dir", temp_dir),
                patch.object(settings, "price_stock_chunk_size", 3),
                patch.object(settings, "price_stock_chunk_pause_seconds", 0),
                patch.object(settings, "price_stock_max_concurrency", 2),
                patch.object(settings, "job_progress_update_every", 3),
            ):
                outcome = await process_price_stock_job("job", "99", "unused.xlsx")
            self.assertTrue(Path(temp_dir, "job_price_stock_result.json").exists())
        self.assertEqual([r["item_id"] for r in outcome["results"]], [r["item_id"] for r in rows])
        self.assertEqual(outcome["summary"]["success_count"], 7)
        self.assertEqual(maximum, 2)
        progress_rows = [c.kwargs.get("processed_rows") for c in progress.call_args_list
                         if "processed_rows" in c.kwargs]
        self.assertEqual(progress_rows, [3, 6, 7, 7])
        self.assertFalse(any("Esperando" in str(c.kwargs.get("message")) for c in progress.call_args_list))


class RollingWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_window_waits_until_oldest_request_expires_instead_of_120_seconds(self):
        limiter = RedisWindowRateLimiter("redis://localhost:6379/0", "test", 10,
                                         max_requests_per_window=2, window_seconds=60, cooldown_seconds=120)
        pipeline = AsyncMock()
        pipeline.get = AsyncMock(side_effect=[None, None])
        pipeline.zcount = AsyncMock(return_value=2)
        pipeline.zrangebyscore = AsyncMock(return_value=[("request", 41.0)])
        limiter.client = Mock()
        limiter.client.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipeline)
        limiter.client.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)

        class StopAfterWait(Exception):
            pass

        with (
            patch("services.redis_rate_limiter.time.time", return_value=100.0),
            patch("services.redis_rate_limiter.asyncio.sleep", new=AsyncMock(side_effect=StopAfterWait)) as sleep,
            self.assertRaises(StopAfterWait),
        ):
            await limiter.acquire()
        self.assertAlmostEqual(sleep.await_args.args[0], 1.001, places=3)
        pipeline.set.assert_not_called()


class DeploymentProfileTests(unittest.TestCase):
    def test_effective_profile_uses_the_live_shared_limiter(self):
        profile = checked_profile()
        self.assertEqual(profile["write_namespace"], "ml:write:global")
        self.assertEqual(profile["write_max_requests_per_window"], settings.ml_write_max_requests_per_window)
        self.assertEqual(profile["price_stock_chunk_size"], settings.price_stock_chunk_size)

    def test_mixed_loaded_policy_is_rejected(self):
        with patch.object(settings, "ml_write_max_requests_per_window", settings.ml_write_max_requests_per_window + 1):
            with self.assertRaisesRegex(RuntimeError, "limitador"):
                checked_profile()

    def test_separate_price_stock_budget_is_rejected(self):
        with patch("scripts.check_price_stock_config.PRICE_STOCK_WRITE_RATE_LIMITER", Mock()):
            with self.assertRaisesRegex(RuntimeError, "global"):
                checked_profile()

    def test_invalid_write_and_progress_configuration_fails_early(self):
        for name in ("ml_write_requests_per_second", "ml_write_max_requests_per_window",
                     "ml_write_window_seconds", "job_progress_update_every"):
            with self.subTest(setting=name), self.assertRaises(ValidationError):
                Settings(_env_file=None, **{name: 0})


if __name__ == "__main__":
    unittest.main()
