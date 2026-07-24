import os
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import settings
from services.job_store import JobStore
from services.publication_export_store import publication_export_store
from services.publication_sync_store import publication_sync_store
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.supabase_publications_store import supabase_publications_store
from tasks.publication_export_tasks import export_publications_task
from tasks.publication_sync_tasks import sync_publications_task

router = APIRouter(prefix="/publications", tags=["publications"])


class PublicationExportRequest(BaseModel):
    publication_date: date


async def _get_connected_ml_user_id() -> str:
    rows = await supabase_meli_connection_store.list_rows(include_tokens=False)
    if not rows:
        raise HTTPException(
            status_code=401,
            detail="No hay una cuenta de Mercado Libre conectada.",
        )

    row = rows[0]
    user_id = row.get("ml_user_id")
    if not row.get("is_active") or not user_id:
        raise HTTPException(
            status_code=401,
            detail="La cuenta de Mercado Libre no está conectada.",
        )

    return str(user_id)


async def _get_sync_state() -> dict:
    state = publication_sync_store.get_state()
    state["has_publications"] = await supabase_publications_store.has_rows()
    return state


def _export_job_response(job: dict) -> dict:
    result_path = str(job.get("result_path") or "")
    return {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "progress": int(job.get("progress") or 0),
        "message": job.get("message"),
        "publication_date": job.get("publication_date"),
        "total_rows": int(job.get("total_rows") or 0),
        "processed_rows": int(job.get("processed_rows") or 0),
        "descriptions_found": int(job.get("descriptions_found") or 0),
        "descriptions_missing": int(job.get("descriptions_missing") or 0),
        "descriptions_failed": int(job.get("descriptions_failed") or 0),
        "cache_hits": int(job.get("cache_hits") or 0),
        "api_items_queried": int(job.get("api_items_queried") or 0),
        "retry_count": int(job.get("retry_count") or 0),
        "recovery_count": int(job.get("recovery_count") or 0),
        "requests_per_second": float(
            job.get("requests_per_second")
            or settings.ml_publication_export_requests_per_second
        ),
        "http_concurrency": int(
            job.get("http_concurrency")
            or settings.ml_publication_export_concurrency
        ),
        "filename": job.get("output_filename") or job.get("filename"),
        "download_ready": (
            job.get("status") == "success"
            and bool(result_path)
            and os.path.exists(result_path)
        ),
        "last_error": job.get("last_error"),
    }


def _existing_export_job(
    *,
    user_id: str,
    creation_date: str,
    total_rows: int,
) -> dict | None:
    existing_job_id = publication_export_store.get_referenced_job_id(
        user_id=user_id,
        creation_date=creation_date,
    )
    if not existing_job_id:
        return None

    job = JobStore.get(existing_job_id)
    active_statuses = {"queued", "processing", "retrying"}
    if job and job.get("status") in active_statuses:
        return job

    result_path = str((job or {}).get("result_path") or "")
    if (
        job
        and job.get("status") == "success"
        and int(job.get("total_rows") or 0) == total_rows
        and result_path
        and os.path.exists(result_path)
    ):
        return job

    publication_export_store.release_reference(
        user_id=user_id,
        creation_date=creation_date,
        job_id=existing_job_id,
    )
    return None


def _recover_stale_export_if_needed(job: dict) -> dict:
    status = job.get("status")
    if status not in {"processing", "retrying"}:
        return job

    now = time.time()
    next_retry_at = float(job.get("next_retry_at") or 0)
    if status == "retrying" and next_retry_at > now:
        return job

    heartbeat_at = float(job.get("heartbeat_at") or 0)
    stale_after = int(
        settings.ml_publication_export_recovery_stale_seconds
    )
    if not heartbeat_at or now - heartbeat_at < stale_after:
        return job

    job_id = str(job.get("id") or "")
    if not job_id or publication_export_store.has_run_lock(job_id):
        return job
    if not publication_export_store.try_acquire_recovery_guard(job_id):
        return job

    user_id = str(job.get("ml_user_id") or "")
    creation_date = str(job.get("publication_date") or "")
    if not user_id or not creation_date:
        return job

    try:
        async_result = export_publications_task.delay(
            job_id,
            user_id,
            creation_date,
        )
    except Exception as exc:
        JobStore.update(
            job_id,
            status="retrying",
            heartbeat_at=now,
            next_retry_at=now + 60,
            message=(
                "El worker no esta disponible. Se volvera a intentar "
                "recuperar la exportacion."
            ),
            last_error=str(exc),
        )
    else:
        JobStore.update(
            job_id,
            status="queued",
            task_id=async_result.id,
            heartbeat_at=now,
            next_retry_at=None,
            recovery_count=int(job.get("recovery_count") or 0) + 1,
            message=(
                "Se detecto una interrupcion y la exportacion fue "
                "reencolada desde su ultimo checkpoint."
            ),
        )

    return JobStore.get(job_id) or job


@router.post("/sync")
async def start_publications_sync():
    user_id = await _get_connected_ml_user_id()
    if not publication_sync_store.try_start(user_id=user_id):
        state = await _get_sync_state()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una carga de publicaciones en ejecución.",
                "state": state,
            },
        )

    try:
        async_result = sync_publications_task.delay(user_id)
    except Exception as exc:
        publication_sync_store.reset(
            message="No se pudo encolar la carga de publicaciones."
        )
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo encolar la carga de publicaciones: {exc}",
        ) from exc

    publication_sync_store.update(
        task_id=async_result.id,
        message="Carga de publicaciones encolada correctamente.",
    )
    return await _get_sync_state()


@router.get("/sync/status")
async def get_publications_sync_status():
    return await _get_sync_state()


@router.post("/export")
async def start_publications_export(payload: PublicationExportRequest):
    user_id = await _get_connected_ml_user_id()
    creation_date = payload.publication_date.isoformat()
    total_rows = await supabase_publications_store.count_by_creation_date(
        creation_date
    )
    if total_rows == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                "No existen publicaciones para la fecha seleccionada "
                f"({creation_date})."
            ),
        )

    existing_job = _existing_export_job(
        user_id=user_id,
        creation_date=creation_date,
        total_rows=total_rows,
    )
    if existing_job:
        recovered_job = _recover_stale_export_if_needed(existing_job)
        return _export_job_response(recovered_job)

    filename = f"publicaciones_{creation_date}.xlsx"
    job = JobStore.create(filename)
    if not publication_export_store.claim_reference(
        user_id=user_id,
        creation_date=creation_date,
        job_id=job["id"],
    ):
        JobStore.delete(job["id"])
        existing_job = _existing_export_job(
            user_id=user_id,
            creation_date=creation_date,
            total_rows=total_rows,
        )
        if existing_job:
            return _export_job_response(existing_job)
        raise HTTPException(
            status_code=409,
            detail=(
                "Otra solicitud de exportacion se inicio al mismo tiempo. "
                "Intenta nuevamente."
            ),
        )

    now = time.time()
    JobStore.update(
        job["id"],
        status="queued",
        progress=0,
        message=(
            f"Exportacion encolada para {total_rows} publicaciones."
        ),
        publication_date=creation_date,
        total_rows=total_rows,
        processed_rows=0,
        descriptions_found=0,
        descriptions_missing=0,
        descriptions_failed=0,
        cache_hits=0,
        api_items_queried=0,
        retry_count=0,
        recovery_count=0,
        requests_per_second=(
            settings.ml_publication_export_requests_per_second
        ),
        http_concurrency=settings.ml_publication_export_concurrency,
        batch_size=settings.ml_publication_export_batch_size,
        ml_user_id=user_id,
        queued_at=now,
        heartbeat_at=now,
        output_filename=filename,
        job_type="publication_export",
    )

    try:
        async_result = export_publications_task.delay(
            job["id"],
            user_id,
            creation_date,
        )
    except Exception as exc:
        JobStore.update(
            job["id"],
            status="error",
            message="No se pudo encolar la exportacion.",
            last_error=str(exc),
        )
        publication_export_store.release_reference(
            user_id=user_id,
            creation_date=creation_date,
            job_id=job["id"],
        )
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo encolar la exportacion: {exc}",
        ) from exc

    JobStore.update(job["id"], task_id=async_result.id)
    queued_job = JobStore.get(job["id"])
    return _export_job_response(queued_job or job)


@router.get("/export/{job_id}")
async def get_publications_export(job_id: str):
    job = JobStore.get(job_id)
    if not job or job.get("job_type") != "publication_export":
        raise HTTPException(
            status_code=404,
            detail="Exportacion de publicaciones no encontrada.",
        )
    recovered_job = _recover_stale_export_if_needed(job)
    return _export_job_response(recovered_job)


@router.get("/export/{job_id}/download")
async def download_publications_export(job_id: str):
    job = JobStore.get(job_id)
    if not job or job.get("job_type") != "publication_export":
        raise HTTPException(
            status_code=404,
            detail="Exportacion de publicaciones no encontrada.",
        )
    if job.get("status") != "success":
        raise HTTPException(
            status_code=409,
            detail="El Excel de publicaciones aun no esta disponible.",
        )

    result_path = str(job.get("result_path") or "")
    if not result_path or not os.path.exists(result_path):
        raise HTTPException(
            status_code=404,
            detail="No se encontro el Excel generado.",
        )

    uploads_root = Path(settings.upload_dir).resolve()
    resolved_result = Path(result_path).resolve()
    if uploads_root not in resolved_result.parents:
        raise HTTPException(
            status_code=400,
            detail="Ruta de descarga invalida.",
        )

    filename = str(
        job.get("output_filename")
        or f"publicaciones_{job.get('publication_date')}.xlsx"
    )
    return FileResponse(
        path=resolved_result,
        filename=filename,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
