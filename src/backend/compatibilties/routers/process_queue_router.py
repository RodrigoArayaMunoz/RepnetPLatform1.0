from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    return process_queue_store.get_state()
