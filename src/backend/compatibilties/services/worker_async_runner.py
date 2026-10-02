"""Keep async connections on one event loop per Celery prefork process.

Calling asyncio.run for every task closes the loop while module-level Redis
pools survive. The next task then reuses connections and locks on that closed
loop. All synchronous task entry points must use this runner instead.
"""

import asyncio
import contextvars
import logging
import os
from collections.abc import Coroutine
from typing import Any, TypeVar

from celery.signals import worker_process_shutdown, worker_shutdown

logger = logging.getLogger(__name__)
T = TypeVar("T")


class WorkerAsyncRunner:
    def __init__(self) -> None:
        self._runner: asyncio.Runner | None = None
        self._pid: int | None = None

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        pid = os.getpid()
        if self._runner is None or self._pid != pid:
            # Initialize lazily in the pool child, never reuse an inherited
            # parent runner after fork. Prefork runs one task at a time here.
            self._runner = asyncio.Runner()
            self._pid = pid
        # Reuse the loop, but keep context variables isolated between tasks.
        return self._runner.run(coroutine, context=contextvars.copy_context())

    def close(self) -> None:
        runner = self._runner
        self._runner = None
        if runner is not None and self._pid == os.getpid():
            runner.close()


_worker_runner = WorkerAsyncRunner()


def run_worker_coroutine(coroutine: Coroutine[Any, Any, T]) -> T:
    return _worker_runner.run(coroutine)


@worker_process_shutdown.connect
@worker_shutdown.connect
def close_worker_async_runner(**kwargs) -> None:
    try:
        _worker_runner.close()
    except Exception:
        logger.exception("[WORKER_ASYNC_RUNNER][SHUTDOWN_ERROR]")
