import unittest
from unittest.mock import AsyncMock, Mock, patch

from services.runtime_recovery_service import reset_runtime_state


class RuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_requeues_processing_rows_and_clears_states(self):
        processing_rows = [{"id": "row-1"}, {"id": "row-2"}]

        with (
            patch(
                "services.runtime_recovery_service."
                "supabase_process_store._table_request",
                new=AsyncMock(
                    side_effect=[processing_rows, processing_rows]
                ),
            ) as table_request,
            patch(
                "services.runtime_recovery_service."
                "process_queue_store.reset",
                new=Mock(),
            ) as reset_process_queue,
            patch(
                "services.runtime_recovery_service."
                "publication_sync_store.reset",
                new=Mock(),
            ) as reset_publication_sync,
            patch(
                "services.runtime_recovery_service."
                "supabase_process_store.list_pending_processes",
                new=AsyncMock(return_value=processing_rows),
            ),
            patch(
                "services.runtime_recovery_service."
                "supabase_meli_connection_store.list_rows",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await reset_runtime_state()

        self.assertEqual(result["processes_requeued"], 2)
        self.assertEqual(table_request.await_count, 2)
        self.assertEqual(
            table_request.await_args_list[1].kwargs["json_body"],
            {"estado": "Pendiente"},
        )
        reset_process_queue.assert_called_once()
        reset_publication_sync.assert_called_once()

    async def test_reset_skips_database_update_without_processing_rows(self):
        with (
            patch(
                "services.runtime_recovery_service."
                "supabase_process_store._table_request",
                new=AsyncMock(return_value=[]),
            ) as table_request,
            patch(
                "services.runtime_recovery_service."
                "process_queue_store.reset",
                new=Mock(),
            ),
            patch(
                "services.runtime_recovery_service."
                "publication_sync_store.reset",
                new=Mock(),
            ),
            patch(
                "services.runtime_recovery_service."
                "supabase_process_store.list_pending_processes",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "services.runtime_recovery_service."
                "supabase_meli_connection_store.list_rows",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await reset_runtime_state()

        self.assertEqual(result["processes_requeued"], 0)
        table_request.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
