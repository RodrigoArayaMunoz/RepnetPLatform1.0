from fastapi import APIRouter, File, HTTPException, UploadFile

from config import settings
from services.excel_service import save_upload_file
from services.job_store import JobStore
from tasks.item_pictures_tasks import process_item_pictures_task

router = APIRouter(prefix="/imports", tags=["item-pictures"])


@router.post("/item-pictures")
async def create_item_pictures_job(
    user_id: str,
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")

    job = JobStore.create(file.filename)
    saved_path = await save_upload_file(file, settings.upload_dir)
    JobStore.update(job["id"], xlsx_path=saved_path)

    async_result = process_item_pictures_task.delay(
        job["id"],
        user_id,
        saved_path,
    )
    JobStore.update(job["id"], task_id=async_result.id)

    return {
        "job_id": job["id"],
        "task_id": async_result.id,
        "status": "queued",
        "message": "Proceso de actualización de fotos encolado",
    }


@router.get("/item-pictures/{job_id}")
async def get_item_pictures_job(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job
