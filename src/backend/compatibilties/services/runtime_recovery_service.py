from typing import Any

from services.process_queue_store import process_queue_store
from services.publication_sync_store import publication_sync_store
from services.supabase_meli_connection_store import (
    supabase_meli_connection_store,
)
from services.supabase_process_store import supabase_process_store


async def reset_runtime_state(
    *,
    resume_process_queue: bool = False,
    resume_publication_sync: bool = False,
) -> dict[str, Any]:
    processing_rows = await supabase_process_store._table_request(
        "GET",
        params={
            "select": "id",
            "estado": "eq.Procesando",
        },
    )
    processing_rows = processing_rows if isinstance(processing_rows, list) else []

    if processing_rows:
        await supabase_process_store._table_request(
            "PATCH",
            params={"estado": "eq.Procesando"},
            json_body={"estado": "Pendiente"},
            return_representation=True,
        )

    process_queue_store.reset(
        message="Cola reiniciada por mantenimiento; procesos pendientes listos para retomar.",
    )
    publication_sync_store.reset(
        message="Carga de publicaciones reiniciada por mantenimiento.",
    )

    pending_processes = await supabase_process_store.list_pending_processes()
    connection_rows = await supabase_meli_connection_store.list_rows(
        include_tokens=False,
    )
    active_connection = next(
        (
            row
            for row in connection_rows
            if row.get("is_active") and row.get("ml_user_id")
        ),
        None,
    )
    ml_user_id = str((active_connection or {}).get("ml_user_id") or "")

    process_queue_resumed = False
    publication_sync_resumed = False

    if resume_process_queue and pending_processes and ml_user_id:
        from tasks.process_queue_tasks import run_process_queue_task

        if process_queue_store.try_start(user_id=ml_user_id):
            async_result = run_process_queue_task.delay(ml_user_id)
            process_queue_store.update(
                task_id=async_result.id,
                message="Cola reanudada después del mantenimiento.",
            )
            process_queue_resumed = True

    if resume_publication_sync and ml_user_id:
        from tasks.publication_sync_tasks import sync_publications_task

        if publication_sync_store.try_start(user_id=ml_user_id):
            async_result = sync_publications_task.delay(ml_user_id)
            publication_sync_store.update(
                task_id=async_result.id,
                message=(
                    "Carga de publicaciones reanudada después del "
                    "mantenimiento."
                ),
            )
            publication_sync_resumed = True

    return {
        "process_queue": "resumed" if process_queue_resumed else "idle",
        "publication_sync": (
            "resumed" if publication_sync_resumed else "idle"
        ),
        "processes_requeued": len(processing_rows),
        "pending_processes": len(pending_processes),
    }
