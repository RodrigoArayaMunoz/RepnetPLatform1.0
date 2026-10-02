import asyncio
import contextvars
import unittest
from unittest.mock import patch

from services.worker_async_runner import WorkerAsyncRunner


class WorkerAsyncRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = WorkerAsyncRunner()
        self.addCleanup(self.runner.close)

    @staticmethod
    async def contend(lock):
        async def use_connection():
            async with lock:
                await asyncio.sleep(0)
                return asyncio.get_running_loop()
        return await asyncio.gather(use_connection(), use_connection())

    def test_old_per_task_loop_reproduces_shared_lock_failure(self):
        lock = asyncio.Lock()
        asyncio.run(self.contend(lock))
        with self.assertRaisesRegex(RuntimeError, "different event loop"):
            asyncio.run(self.contend(lock))

    def test_repeated_tasks_keep_connections_on_the_same_open_loop(self):
        lock = asyncio.Lock()
        loops = [self.runner.run(self.contend(lock))[0] for _ in range(3)]
        self.assertTrue(all(loop is loops[0] for loop in loops))
        self.assertFalse(loops[0].is_closed())
        self.runner.close()
        self.assertTrue(loops[0].is_closed())

    def test_failure_does_not_close_loop_for_the_next_task(self):
        lock = asyncio.Lock()
        initial_loop = self.runner.run(self.contend(lock))[0]

        async def failed_task():
            await self.contend(lock)
            raise ValueError("Expected business error")

        with self.assertRaisesRegex(ValueError, "Expected business error"):
            self.runner.run(failed_task())
        self.assertIs(self.runner.run(self.contend(lock))[0], initial_loop)

    def test_context_variables_do_not_leak_to_the_next_task(self):
        current_account = contextvars.ContextVar("current_account", default=None)

        async def first_task():
            current_account.set("account-1")
            return current_account.get()

        async def second_task():
            return current_account.get()

        self.assertEqual(self.runner.run(first_task()), "account-1")
        self.assertIsNone(self.runner.run(second_task()))
        token = current_account.set("parent-context")
        try:
            self.assertEqual(self.runner.run(second_task()), "parent-context")
        finally:
            current_account.reset(token)

    def test_description_queue_price_and_photo_entry_points_share_the_runner(self):
        from tasks.item_pictures_tasks import process_item_pictures_task
        from tasks.price_stock_tasks import process_price_stock_task
        from tasks.process_queue_tasks import run_process_queue_task

        lock = asyncio.Lock()
        loops = []

        async def task_body(*args, **kwargs):
            loops.append((await self.contend(lock))[0])

        with (
            patch("services.worker_async_runner._worker_runner", self.runner),
            patch("tasks.process_queue_tasks._run_process_queue_task", task_body),
            patch("tasks.price_stock_tasks._process_price_stock_task", task_body),
            patch("tasks.item_pictures_tasks._process_item_pictures_task", task_body),
        ):
            run_process_queue_task.run("99")
            process_price_stock_task.run("price", "99", "prices.xlsx")
            process_item_pictures_task.run("photo", "99", "photos.xlsx")
            run_process_queue_task.run("99")
        self.assertEqual(len(loops), 4)
        self.assertTrue(all(loop is loops[0] for loop in loops))


if __name__ == "__main__":
    unittest.main()
