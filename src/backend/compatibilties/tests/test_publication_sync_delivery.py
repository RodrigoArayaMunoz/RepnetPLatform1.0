import unittest
from unittest.mock import Mock, patch

from celery_app import celery_app
from config import settings
from services.publication_sync_store import publication_sync_store
from tasks.publication_sync_tasks import sync_publications_task


class CeleryVisibilityTimeoutTests(unittest.TestCase):
    def test_redis_visibility_timeout_exceeds_publication_sync_duration(self):
        expected = settings.celery_visibility_timeout_seconds

        self.assertEqual(expected, 12 * 60 * 60)
        self.assertEqual(
            celery_app.conf.broker_transport_options["visibility_timeout"],
            expected,
        )
        self.assertEqual(
            celery_app.conf.result_backend_transport_options["visibility_timeout"],
            expected,
        )
        self.assertEqual(celery_app.conf.visibility_timeout, expected)


class PublicationSyncRedeliveryTests(unittest.TestCase):
    def _run_task(self, *, task_id, redelivered, state):
        task = sync_publications_task._get_current_object()
        task.push_request(
            id=task_id,
            delivery_info={"redelivered": redelivered},
        )
        try:
            sync_job = Mock(return_value=object())
            with (
                patch.object(publication_sync_store, "get_state", return_value=state),
                patch(
                    "tasks.publication_sync_tasks._sync_publications_task",
                    new=sync_job,
                ),
                patch("tasks.publication_sync_tasks.asyncio.run") as asyncio_run,
            ):
                task.run("2682261950")
                return asyncio_run
        finally:
            task.pop_request()

    def test_completed_redelivery_is_skipped(self):
        asyncio_run = self._run_task(
            task_id="task-1",
            redelivered=True,
            state={
                "running": False,
                "task_id": "task-1",
            },
        )

        asyncio_run.assert_not_called()

    def test_original_delivery_still_runs(self):
        asyncio_run = self._run_task(
            task_id="task-1",
            redelivered=False,
            state={
                "running": True,
                "task_id": "task-1",
            },
        )

        asyncio_run.assert_called_once()

    def test_legitimate_redelivery_after_worker_loss_can_resume(self):
        asyncio_run = self._run_task(
            task_id="task-1",
            redelivered=True,
            state={
                "running": True,
                "task_id": "task-1",
            },
        )

        asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
