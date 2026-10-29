import unittest
from unittest.mock import AsyncMock, patch

import httpx

from config import settings
from services.process_queue_service import run_process_queue
from services.supabase_process_store import SupabaseProcessStore


RealClient = httpx.AsyncClient


class ProcessQueueFifoTests(unittest.IsolatedAsyncioTestCase):
    async def run_queue_with_new_arrivals(self, *, fail_second=False):
        # The UUIDs deliberately do not follow arrival order. C and D share
        # a timestamp; their persisted process numbers resolve that tie.
        def row(row_id, number, date, hour, kind):
            return dict(id=row_id, proceso_id=number, fecha_proceso=date,
                        hora_proceso=hour, archivo=f"{kind}.xlsx",
                        estado="Pendiente", kind=kind)

        first = row("b-first", "00005", "2026-10-28", "23:58:00", "compatibilities")
        second = row("a-second", "00006", "2026-10-28", "23:59:00", "price_stock")
        third = row("z-third", "00007", "2026-10-29", "00:00:00", "item_pictures")
        fourth = row("c-fourth", "00008", "2026-10-29", "00:00:00", "item_descriptions")
        fifth = row("d-fifth", "00009", "2026-10-29", "00:01:00", "compatibility_exceptions")
        rows = [first]
        executed = []

        def respond(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.params["estado"], "eq.Pendiente")
            pending = [dict(item) for item in rows if item["estado"] == "Pendiente"]
            # Honor the actual REST sort, rather than returning a preordered
            # mock: reverting the worker to newest-first must fail this test.
            for term in reversed(request.url.params["order"].split(",")):
                column, direction = term.split(".")
                pending.sort(key=lambda item: item[column], reverse=direction == "desc")
            return httpx.Response(200, json=pending)

        async def execute(*, process_row, user_id):
            self.assertEqual(user_id, "99")
            executed.append(process_row["id"])
            if process_row["id"] == first["id"]:
                rows.extend([second, third])  # Added while the first file runs.
            elif process_row["id"] == second["id"]:
                rows.append(fourth)  # Must stay behind the older third file.
                if fail_second:
                    raise RuntimeError("Test import failed")
            return process_row["kind"], "internal-job", {"error_count": 0}

        async def save_status(row_id, status):
            next(item for item in rows if item["id"] == row_id)["estado"] = status

        async def between_files(_delay):
            if executed[-1] == third["id"]:
                rows.append(fifth)  # Also include arrivals during the pause.

        store = SupabaseProcessStore()
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_service_role_key", "test-only"),
            patch("services.supabase_process_store.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
            patch("services.process_queue_service.supabase_process_store", store),
            patch.object(store, "update_process_status", side_effect=save_status),
            patch("services.process_queue_service._execute_process_record", side_effect=execute),
            patch("services.process_queue_service.ml_client.startup", new=AsyncMock()),
            patch("services.process_queue_service.ml_client.shutdown", new=AsyncMock()),
            patch("services.process_queue_service.supabase_meli_connection_store.restore_token_store", new=AsyncMock()),
            patch("services.process_queue_service.JobStore.get", return_value={}),
            patch("services.process_queue_service.process_queue_store") as queue,
            patch("services.process_queue_service.process_queue_error_store"),
            patch("services.process_queue_service.process_queue_result_store"),
            patch("services.process_queue_service.asyncio.sleep", side_effect=between_files),
            patch("services.process_queue_service.logger"),
        ):
            await run_process_queue(user_id="99")

        self.assertEqual(executed, [first["id"], second["id"], third["id"], fourth["id"], fifth["id"]])
        self.assertEqual([item["estado"] for item in rows], [
            "Procesado", "Error" if fail_second else "Procesado", "Procesado", "Procesado", "Procesado",
        ])
        queue.finish.assert_called_once()

    async def test_new_arrivals_follow_older_pending_files_across_process_types(self):
        await self.run_queue_with_new_arrivals()

    async def test_failed_file_does_not_reorder_or_repeat_remaining_files(self):
        await self.run_queue_with_new_arrivals(fail_second=True)
