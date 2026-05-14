import re
import time
from io import BytesIO
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openpyxl import Workbook
from openpyxl.styles import Font

from celery.states import READY_STATES
from celery_app import celery_app
from services.job_store import JobStore
from services.process_queue_error_store import process_queue_error_store
from services.process_queue_store import process_queue_store
from tasks.process_queue_tasks import run_process_queue_task

router = APIRouter(prefix="/process-queue", tags=["process-queue"])

STALE_QUEUE_TASK_GRACE_SECONDS = 5 * 60


class StartProcessQueueRequest(BaseModel):
    user_id: str


class ProcessQueueErrorSummaryRequest(BaseModel):
    row_ids: list[str]


def _sanitize_export_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "errores_mlc"


def _build_failed_items_excel(
    failed_items: list[str],
) -> BytesIO:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "MLC con error"
    worksheet.append(["MLC"])
    worksheet["A1"].font = Font(bold=True)

    for item_id in failed_items:
        worksheet.append([item_id])

    worksheet.column_dimensions["A"].width = 24

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _should_reset_stale_queue(state: dict) -> bool:
    if not state.get("running"):
        return True

    task_id = state.get("task_id")
    started_at = state.get("started_at")
    age_seconds = None

    if isinstance(started_at, (int, float)):
        age_seconds = max(0, time.time() - float(started_at))

    if not task_id:
        return bool(
            age_seconds is not None and age_seconds >= STALE_QUEUE_TASK_GRACE_SECONDS
        )

    task_state = celery_app.AsyncResult(str(task_id)).state
    if task_state in READY_STATES:
        return True

    if task_state == "PENDING":
        return bool(
            age_seconds is not None and age_seconds >= STALE_QUEUE_TASK_GRACE_SECONDS
        )

    return False


@router.post("/start")
async def start_process_queue(payload: StartProcessQueueRequest):
    current_state = process_queue_store.get_state()
    if _should_reset_stale_queue(current_state):
        process_queue_store.reset(
            message="Cola reiniciada para retomar procesos pendientes",
        )

    started = process_queue_store.try_start(user_id=payload.user_id)
    if not started:
        raise HTTPException(
            status_code=409,
            detail="Ya existe una cola de procesos en ejecución",
        )

    try:
        async_result = run_process_queue_task.delay(str(payload.user_id))
    except Exception:
        process_queue_store.finish(
            message="No se pudo iniciar la cola de procesos",
            last_error="No se pudo iniciar la cola de procesos",
        )
        raise

    process_queue_store.update(
        task_id=async_result.id,
        message="Cola de procesos encolada correctamente",
    )

    return {
        "ok": True,
        "task_id": async_result.id,
        "status": "queued",
        "message": "Cola de procesos iniciada",
    }


@router.get("/status")
async def get_process_queue_status():
    state = process_queue_store.get_state()

    current_job_id = state.get("current_job_id")
    if current_job_id and state.get("running"):
        job = JobStore.get(current_job_id)
        if job:
            state["job_processed_rows"] = job.get("processed_rows", 0)
            state["job_total_rows"] = job.get("total_rows", 0)
            state["job_progress"] = job.get("progress", 0)
        else:
            state["job_processed_rows"] = 0
            state["job_total_rows"] = 0
            state["job_progress"] = 0
    else:
        state["job_processed_rows"] = 0
        state["job_total_rows"] = 0
        state["job_progress"] = 0

    return state


@router.get("/errors/{row_id}")
async def get_process_queue_error(row_id: str):
    error_payload = process_queue_error_store.get(row_id)
    if not error_payload:
        raise HTTPException(
            status_code=404,
            detail="No se encontraron detalles de error para ese proceso.",
        )

    return error_payload


@router.get("/errors/{row_id}/export")
async def export_process_queue_error_items(row_id: str):
    error_payload = process_queue_error_store.get(row_id)
    if not error_payload:
        raise HTTPException(
            status_code=404,
            detail="No se encontraron detalles de error para ese proceso.",
        )

    failed_items = [
        str(item_id).strip()
        for item_id in (error_payload.get("failed_items") or [])
        if str(item_id).strip()
    ]
    if not failed_items:
        raise HTTPException(
            status_code=400,
            detail="Ese proceso no tiene MLC con error para exportar.",
        )

    file_buffer = _build_failed_items_excel(failed_items)
    process_id = str(
        error_payload.get("process_id")
        or error_payload.get("process_row_id")
        or row_id
    )
    safe_process_id = _sanitize_export_filename(process_id)
    filename = f"mlc_con_error_{safe_process_id}.xlsx"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        file_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.post("/errors/summaries")
async def get_process_queue_error_summaries(
    payload: ProcessQueueErrorSummaryRequest,
) -> dict[str, Any]:
    row_ids = [str(row_id).strip() for row_id in payload.row_ids if str(row_id).strip()]
    if not row_ids:
        return {"items": {}}

    error_payloads = process_queue_error_store.get_many(row_ids)
    items: dict[str, Any] = {}

    for row_id, error_payload in error_payloads.items():
        failed_items = error_payload.get("failed_items")
        is_partial = isinstance(failed_items, list) and len(failed_items) > 0
        items[row_id] = {
            "has_error_details": True,
            "is_partial": is_partial,
            "display_status": "Procesado con Errores" if is_partial else "Error",
        }

    return {"items": items}
