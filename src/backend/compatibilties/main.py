import json
import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse

from config import settings
from schemas import JobResponse
from services import job_store
from services.ml_publicationswithout_service import ml_publications_service
from services.token_store import token_store, require_ml_env
from services.job_store import JobStore
from services.excel_service import save_upload_file, load_excel_rows
from services.ml_client import ml_client
from tasks.import_tasks import process_excel_job
from routers.product_resolution_router import router as product_resolution_router
from routers.compatibility_batch_router import router as compatibility_batch_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.upload_dir, exist_ok=True)
    await ml_client.startup()
    yield
    await ml_client.shutdown()


app = FastAPI(title="Compatibilidades API", lifespan=lifespan)

app.include_router(product_resolution_router)
app.include_router(compatibility_batch_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        settings.frontend_url,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ml/status")
async def ml_status():
    user_id = token_store.first_user_id()
    if not user_id:
        return {"connected": False}

    token_data = token_store.get(user_id)
    if not token_data:
        return {"connected": False}

    try:
        await ml_client.get_valid_token(user_id)
        token_data = token_store.get(user_id)
        return {
            "connected": True,
            "user_id": user_id,
            "has_refresh_token": bool(token_data.get("refresh_token")),
            "expires_in": token_data.get("expires_in"),
            "expires_at": token_data.get("expires_at"),
        }
    except HTTPException:
        return {"connected": False}


@app.get("/ml/me")
async def ml_me(user_id: int):
    access_token = await ml_client.get_valid_token(user_id)
    data = await ml_client.request("GET", "/users/me", access_token)
    return data


@app.get("/auth/login")
def ml_auth_login(state: str | None = None):
    require_ml_env()
    params = {
        "response_type": "code",
        "client_id": settings.ml_client_id,
        "redirect_uri": settings.ml_redirect_uri,
    }
    if state:
        params["state"] = state

    url = f"{settings.ml_auth_url}?{urlencode(params)}"
    return RedirectResponse(url=url)


@app.get("/auth/callback")
async def ml_auth_callback(code: str = Query(...), state: str | None = None):
    require_ml_env()

    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.ml_client_id,
        "client_secret": settings.ml_client_secret,
        "code": code,
        "redirect_uri": settings.ml_redirect_uri,
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "ngrok-skip-browser-warning": "any",
    }

    if not ml_client.client:
        raise HTTPException(status_code=500, detail="HTTP client no inicializado")

    r = await ml_client.client.post(settings.ml_token_url, data=payload, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    token_response = r.json()

    user_id = token_response.get("user_id")
    if not user_id:
        raise HTTPException(status_code=500, detail="No se recibió user_id desde Mercado Libre")

    payload_to_save = token_store.build_payload(token_response, user_id)
    token_store.set(user_id, payload_to_save)

    return RedirectResponse(url=f"{settings.frontend_url}?ml_connected=1&user_id={user_id}")


@app.post("/auth/refresh")
async def ml_refresh_token(user_id: int):
    new_token_data = await ml_client.refresh_token(user_id)
    return {
        "ok": True,
        "user_id": int(user_id),
        "expires_at": new_token_data.get("expires_at"),
        "expires_in": new_token_data.get("expires_in"),
        "message": "Token renovado correctamente",
    }


@app.post("/auth/logout")
async def ml_logout(user_id: int):
    token_store.remove(user_id)
    return {"ok": True, "message": "Sesión local eliminada"}


@app.post("/imports-excel", response_model=JobResponse)
async def upload_excel(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")

    filename_lower = file.filename.lower()
    if not filename_lower.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(
            status_code=400,
            detail="Formato no permitido. Solo se aceptan archivos .xlsx, .xls o .csv",
        )

    try:
        job = JobStore.create(file.filename)

        saved_path = await save_upload_file(file, settings.upload_dir)
        rows = load_excel_rows(saved_path)

        JobStore.update(
            job["id"],
            status="uploaded",
            message="Archivo cargado correctamente. Listo para procesar.",
            progress=0,
            xlsx_path=saved_path,
            total_rows=len(rows),
            processed_rows=0,
        )

        job = JobStore.get(job["id"])
        if not job:
            raise HTTPException(status_code=500, detail="No se pudo recuperar el job creado")

        return JobResponse(
            job_id=job["id"],
            status=job.get("status", "uploaded"),
            message=job.get("message", "Archivo cargado correctamente"),
            progress=job.get("progress", 0),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar el archivo: {str(e)}")

@app.post("/imports/{job_id}/start", response_model=JobResponse)
async def start_processing(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no existe")

    if job.get("status") in ("processing", "success"):
        return JobResponse(
            job_id=job_id,
            status=job.get("status", "pending"),
            message=job.get("message", ""),
            progress=job.get("progress", 0),
        )

    if not job.get("xlsx_path"):
        raise HTTPException(status_code=400, detail="Excel no disponible")

    user_id = token_store.first_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="No hay cuenta de Mercado Libre conectada")

    JobStore.update(
        job_id,
        status="queued",
        message="Procesamiento en cola...",
        progress=0,
    )

    task = process_excel_job.delay(job_id, str(user_id))

    JobStore.update(job_id, task_id=task.id)

    job = JobStore.get(job_id)
    return JobResponse(
        job_id=job_id,
        status=job.get("status", "queued"),
        message=job.get("message", "Procesamiento en cola..."),
        progress=job.get("progress", 0),
    )

@app.get("/imports/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no existe")

    return JobResponse(
        job_id=job_id,
        status=job.get("status", "pending"),
        message=job.get("message", ""),
        progress=job.get("progress", 0),
    )

@app.get("/imports/{job_id}/result")
async def get_job_result(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no existe")
    if job.get("status") != "success":
        raise HTTPException(status_code=400, detail="El job aún no finaliza correctamente")

    result_path = job.get("result_path")
    if not result_path or not os.path.exists(result_path):
        raise HTTPException(status_code=404, detail="No se encontró archivo de resultado")

    with open(result_path, "r", encoding="utf-8") as f:
        result_data = json.load(f)

    return {
        "ok": True,
        "job_id": job_id,
        "summary": job.get("summary", {}),
        "results": result_data,
    }

@app.get("/imports-excel/{job_id}", response_model=JobResponse)
async def get_import_status(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    return JobResponse(
        job_id=job_id,
        status=job.get("status", "pending"),
        message=job.get("message", ""),
        progress=job.get("progress", 0),
    )

@app.get("/publications/without-compatibilities")
async def get_publications_without_compatibilities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=20),
    q: str = Query(""),
    refresh: bool = Query(False),
):
    user_id = token_store.first_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="No hay cuenta de Mercado Libre conectada")

    return await ml_publications_service.get_publications_without_compatibilities(
        user_id=str(user_id),
        page=page,
        page_size=page_size,
        q=q,
        refresh=refresh,
    )

@app.get("/publications/without-compatibilities/export")
async def export_publications_without_compatibilities(
    q: str = Query(""),
):
    user_id = token_store.first_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="No hay cuenta de Mercado Libre conectada")

    file_buffer, filename = await ml_publications_service.export_publications_without_compatibilities_excel(
        user_id=str(user_id),
        q=q,
    )

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }

    return StreamingResponse(
        file_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

@app.post("/publications/without-compatibilities/refresh")
async def refresh_publications_without_compatibilities():
    user_id = token_store.first_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="No hay cuenta de Mercado Libre conectada")

    return await ml_publications_service.start_background_refresh(
        user_id=str(user_id)
    )


@app.get("/publications/without-compatibilities/refresh-status")
async def get_publications_without_compatibilities_refresh_status():
    user_id = token_store.first_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="No hay cuenta de Mercado Libre conectada")

    return await ml_publications_service.get_refresh_status(
        user_id=str(user_id)
    )



