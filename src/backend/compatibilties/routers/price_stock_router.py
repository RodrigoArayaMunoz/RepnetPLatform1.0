from fastapi import APIRouter, File, HTTPException, UploadFile

from config import settings
from services.excel_service import save_upload_file
from services.job_store import JobStore
from tasks.price_stock_tasks import process_price_stock_task

router = APIRouter(prefix="/imports", tags=["price-stock"])


@router.post("/price-stock")
async def create_price_stock_job(
    user_id: str,
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")

    job = JobStore.create(file.filename)
    saved_path = await save_upload_file(file, settings.upload_dir)
    JobStore.update(job["id"], xlsx_path=saved_path)

    async_result = process_price_stock_task.delay(
        job["id"],
        user_id,
        saved_path,
    )
    JobStore.update(job["id"], task_id=async_result.id)

    return {
        "job_id": job["id"],
        "task_id": async_result.id,
        "status": "queued",
        "message": "Proceso de actualización de precios y stock encolado",
    }


@router.get("/price-stock/{job_id}")
async def get_price_stock_job(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job
