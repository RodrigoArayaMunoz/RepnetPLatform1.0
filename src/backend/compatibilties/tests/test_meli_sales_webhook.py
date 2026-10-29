import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from celery_app import celery_app
from main import app
from tasks.meli_sales_tasks import (
    backfill_meli_sales_task,
    process_meli_notification_task,
)


def forbid_retired_io(stack):
    """Fail a test if retired work reaches storage, a queue, or an HTTP service."""
    mocks = []
    for target in (
        "httpx.AsyncClient.request",
        "redis.asyncio.Redis.execute_command",
        "redis.Redis.execute_command",
        "celery.app.base.Celery.send_task",
    ):
        mocks.append(stack.enter_context(patch(
            target, side_effect=AssertionError("Retired integration attempted I/O"),
        )))
    for task in (process_meli_notification_task, backfill_meli_sales_task):
        mocks.append(stack.enter_context(patch.object(
            task, "apply_async", side_effect=AssertionError("Retired task was queued"),
        )))
    return mocks


class RetiredMercadoLibreHttpTests(unittest.TestCase):
    def test_late_webhooks_are_acknowledged_without_auth_or_io(self):
        with ExitStack() as stack:
            io = forbid_retired_io(stack)
            auth = stack.enter_context(patch("main.verify_supabase_request", new=AsyncMock()))
            client = TestClient(app)
            for payload in (
                {"topic": "items", "resource": "/items/MLC123", "user_id": 99},
                {"topic": "orders_v2", "resource": "/orders/123", "attempts": 8},
                {},
            ):
                response = client.post("/webhooks/mercadolibre", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True, "queued": False, "disabled": True})
            # No parser is needed after retirement, including for stale malformed callbacks.
            response = client.post("/webhooks/mercadolibre", content=b"not-json")
            self.assertEqual(response.status_code, 200)
            auth.assert_not_awaited()
            for call in io:
                call.assert_not_called()

    def test_health_reports_integration_disabled_without_io(self):
        with ExitStack() as stack:
            io = forbid_retired_io(stack)
            response = TestClient(app).get("/webhooks/mercadolibre/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True, "enabled": False})
            for call in io:
                call.assert_not_called()

    def test_all_legacy_sales_routes_stop_before_auth_or_io(self):
        with ExitStack() as stack:
            io = forbid_retired_io(stack)
            auth = stack.enter_context(patch("main.verify_supabase_request", new=AsyncMock()))
            client = TestClient(app)
            for method, path in (
                ("GET", "/ml/sales"),
                ("GET", "/ml/sales/today?date=2026-10-28"),
                ("POST", "/ml/sales/sync"),
                ("GET", "/ml/sales/sync/old-task-id"),
                ("PUT", "/ml/sales/123/picking-status"),
            ):
                with self.subTest(method=method, path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 410)
            auth.assert_not_awaited()
            for call in io:
                call.assert_not_called()


class RetiredMercadoLibreTaskTests(unittest.TestCase):
    def test_legacy_deliveries_and_retries_terminate_without_external_work(self):
        cases = (
            (process_meli_notification_task,
             [{"topic": "items", "user_id": 99}, "event-123"], {}),
            (process_meli_notification_task,
             [], {"payload": {"topic": "orders_v2"}, "event_key": "old-event"}),
            (backfill_meli_sales_task,
             ["99", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"], {}),
            (backfill_meli_sales_task,
             [], {"user_id": "99", "range_start": "old-date", "range_end": "old-date"}),
        )
        for task, args, kwargs in cases:
            with self.subTest(task=task.name, kwargs=kwargs), ExitStack() as stack:
                io = forbid_retired_io(stack)
                retry = stack.enter_context(patch.object(
                    task, "retry", side_effect=AssertionError("Retired task retried"),
                ))
                save = stack.enter_context(patch.object(
                    task.backend, "store_result", side_effect=AssertionError("Retired task saved result"),
                ))
                result = task.apply(args=args, kwargs=kwargs, retries=9, throw=True)
                self.assertTrue(result.successful())
                self.assertIsNone(result.result)
                retry.assert_not_called()
                save.assert_not_called()
                for call in io:
                    call.assert_not_called()
                self.assertTrue(task.ignore_result)
                self.assertFalse(task.track_started)
                self.assertFalse(task.acks_late)
                self.assertEqual(task.max_retries, 0)

    def test_retired_names_are_isolated_from_live_process_queues(self):
        for task_name in ("tasks.process_meli_notification", "tasks.backfill_meli_sales"):
            self.assertIn(task_name, celery_app.tasks)
            self.assertEqual(celery_app.conf.task_routes[task_name]["queue"], "meli_notifications")
        for task_name, queue in (
            ("tasks.run_process_queue_job", "compat_dispatch"),
            ("tasks.process_item_pictures_job", "compat_dispatch"),
            ("tasks.process_price_stock_job", "compat_dispatch"),
            ("tasks.add_compatibilities_batch_job", "compat_chunks"),
            ("tasks.sync_publications_job", "publications_sync"),
            ("tasks.export_publications_job", "publication_exports"),
        ):
            self.assertIn(task_name, celery_app.tasks)
            self.assertEqual(celery_app.conf.task_routes[task_name]["queue"], queue)


if __name__ == "__main__":
    unittest.main()
