from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.job_store import JobStore
from services.process_queue_store import process_queue_store
from tasks.process_queue_tasks import run_process_queue_task

router = APIRouter(prefix="/process-queue", tags=["process-queue"])


class StartProcessQueueRequest(BaseModel):
    user_id: str


@router.post("/start")
async def start_process_queue(payload: StartProcessQueueRequest):
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
