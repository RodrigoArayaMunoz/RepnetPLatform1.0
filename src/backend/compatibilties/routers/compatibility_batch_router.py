from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.job_store import JobStore
from tasks.compatibility_batch_tasks import add_compatibilities_batch_job

router = APIRouter(prefix="/imports", tags=["compatibility-batch"])


class BatchCompatRequest(BaseModel):
    user_id: str
    resolved_job_id: str


@router.post("/add-compatibilities-batch")
async def create_add_compatibilities_batch_job(payload: BatchCompatRequest):
    resolved_job = JobStore.get(payload.resolved_job_id)
    if not resolved_job:
        raise HTTPException(status_code=404, detail="Job de resolución no encontrado")

    resolved_path = resolved_job.get("result_path")
    if not resolved_path:
        raise HTTPException(status_code=400, detail="El job resuelto no tiene result_path")

    job = JobStore.create(f"compat_batch_from_{payload.resolved_job_id}.json")
    async_result = add_compatibilities_batch_job.delay(
        job["id"],
        payload.user_id,
        resolved_path,
    )
    JobStore.update(job["id"], task_id=async_result.id, xlsx_path=resolved_path)

    return {
        "job_id": job["id"],
        "task_id": async_result.id,
        "status": "queued",
        "message": "Carga batch de compatibilidades encolada",
    }


@router.get("/add-compatibilities-batch/{job_id}")
async def get_add_compatibilities_batch_job(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job