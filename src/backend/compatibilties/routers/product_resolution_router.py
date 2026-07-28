from fastapi import APIRouter, File, HTTPException, UploadFile

from config import settings
from services.excel_service import save_upload_file
from services.job_store import JobStore
from tasks.product_resolution_tasks import resolve_products_job

router = APIRouter(prefix="/imports", tags=["resolve-products"])


@router.post("/resolve-products")
async def create_resolve_products_job(
    user_id: str,
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")

    job = JobStore.create(file.filename)
    saved_path = await save_upload_file(file, settings.upload_dir)
    JobStore.update(job["id"], xlsx_path=saved_path)

    async_result = resolve_products_job.delay(job["id"], user_id, "MLC")
    JobStore.update(job["id"], task_id=async_result.id)

    return {
        "job_id": job["id"],
        "task_id": async_result.id,
        "status": "queued",
        "message": "Resolución de familias de vehículos encolada",
    }


@router.get("/resolve-products/{job_id}")
async def get_resolve_products_job(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job
