import base64
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from config import settings
from schemas import JobResponse
from services.auth_guard import is_public_path, verify_supabase_request
from services.ml_publicationswithout_service import ml_publications_service
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.token_store import token_store, require_ml_env
from services.job_store import JobStore
from services.ml_client import ml_client
from routers.product_resolution_router import router as product_resolution_router
from routers.compatibility_batch_router import router as compatibility_batch_router
from routers.compatibility_exception_router import router as compatibility_exception_router
from routers.item_pictures_router import router as item_pictures_router
from routers.price_stock_router import router as price_stock_router
from routers.process_queue_router import router as process_queue_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.upload_dir, exist_ok=True)
    await ml_client.startup()
    await supabase_meli_connection_store.restore_token_store()
    yield
    await ml_client.shutdown()


app = FastAPI(title="Compatibilidades API", lifespan=lifespan)

app.include_router(product_resolution_router)
app.include_router(compatibility_batch_router)
app.include_router(compatibility_exception_router)
app.include_router(item_pictures_router)
app.include_router(price_stock_router)
app.include_router(process_queue_router)

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


@app.middleware("http")
async def require_supabase_auth(request: Request, call_next):
    if (
        not settings.backend_auth_enabled
        or request.method == "OPTIONS"
        or is_public_path(request.url.path)
    ):
        return await call_next(request)

    try:
        request.state.supabase_user = await verify_supabase_request(request)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    return await call_next(request)


def build_safe_frontend_redirect(redirect_to: str | None = None) -> str:
    base_frontend_url = settings.frontend_url.rstrip("/")

    if not redirect_to:
        return f"{base_frontend_url}/"

    parsed = urlparse(redirect_to)
    is_safe_relative_path = (
        not parsed.scheme
        and not parsed.netloc
        and redirect_to.startswith("/")
        and not redirect_to.startswith("//")
    )

    if not is_safe_relative_path:
        return f"{base_frontend_url}/"

    normalized_path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{base_frontend_url}{normalized_path}{query}{fragment}"


def _base64_urlsafe_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _base64_urlsafe_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii"))


def _oauth_state_secret() -> bytes:
    secret = (
        settings.supabase_service_role_key
        or settings.ml_client_secret
        or settings.supabase_anon_key
    )
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="No hay secreto configurado para firmar OAuth state",
        )
    return secret.encode("utf-8")


def build_oauth_state(redirect_to: str | None = None) -> str:
    payload = {
        "redirect_to": redirect_to or "/",
        "exp": int(time.time()) + 10 * 60,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.digest(_oauth_state_secret(), payload_bytes, "sha256")
    return f"{_base64_urlsafe_encode(payload_bytes)}.{_base64_urlsafe_encode(signature)}"


def read_oauth_state_redirect(state: str | None) -> str | None:
    if not state:
        if settings.backend_auth_enabled:
            raise HTTPException(status_code=401, detail="Falta OAuth state")
        return None

    try:
        payload_part, signature_part = state.split(".", 1)
        payload_bytes = _base64_urlsafe_decode(payload_part)
        expected_signature = hmac.digest(_oauth_state_secret(), payload_bytes, "sha256")
        received_signature = _base64_urlsafe_decode(signature_part)
    except Exception:
        if settings.backend_auth_enabled:
            raise HTTPException(status_code=401, detail="OAuth state invalido")
        return state

    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(status_code=401, detail="OAuth state invalido")

    payload = json.loads(payload_bytes)
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="OAuth state expirado")

    return str(payload.get("redirect_to") or "/")


def build_ml_authorization_url(redirect_to: str | None = None) -> str:
    require_ml_env()
    params = {
        "response_type": "code",
        "client_id": settings.ml_client_id,
        "redirect_uri": settings.ml_redirect_uri,
        "state": build_oauth_state(redirect_to),
    }
    return f"{settings.ml_auth_url}?{urlencode(params)}"


@app.get("/ml/status")
async def ml_status():
    rows = await supabase_meli_connection_store.list_rows(include_tokens=False)
    if not rows:
        return {"connected": False}

    row = rows[0]
    user_id = row.get("ml_user_id")
    connected = bool(row.get("is_active") and user_id)

    if connected:
        try:
            # Verifica la sesion real contra ML y fuerza refresh si el access token
            # ya expiro o fue invalidado.
            await ml_client.request("GET", "/users/me", user_id=user_id)
        except HTTPException:
            connected = False

    return {
        "connected": connected,
        "user_id": str(user_id) if connected else None,
    }


@app.get("/ml/me")
async def ml_me(user_id: int):
    access_token = await ml_client.get_valid_token(user_id)
    data = await ml_client.request("GET", "/users/me", access_token)
    return data


@app.get("/auth/login")
def ml_auth_login(redirect_to: str | None = None, state: str | None = None):
    redirect_state = redirect_to or state
    return RedirectResponse(url=build_ml_authorization_url(redirect_state))


@app.get("/auth/login-url")
def ml_auth_login_url(redirect_to: str | None = None):
    return {"url": build_ml_authorization_url(redirect_to)}


@app.get("/auth/callback")
async def ml_auth_callback(code: str = Query(...), state: str | None = None):
    require_ml_env()
    redirect_to = read_oauth_state_redirect(state)

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
        raise HTTPException(status_code=500, detail="No se recibiÃ³ user_id desde Mercado Libre")

    payload_to_save = token_store.build_payload(token_response, user_id)
    token_store.set(user_id, payload_to_save)
    synced = await supabase_meli_connection_store.sync_connection(
        payload_to_save,
        is_active=True,
    )
    if not synced:
        raise HTTPException(
            status_code=500,
            detail="No se pudo guardar la conexion de Mercado Libre en Supabase",
        )

    return RedirectResponse(url=build_safe_frontend_redirect(redirect_to))


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
    await supabase_meli_connection_store.mark_disconnected(user_id)
    return {"ok": True, "message": "SesiÃ³n local eliminada"}


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


@app.get("/imports/{job_id}/detail")
async def get_job_detail(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no existe")
    return job


@app.get("/imports/{job_id}/result")
async def get_job_result(job_id: str):
    job = JobStore.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no existe")

    if job.get("status") != "success":
        raise HTTPException(status_code=400, detail="El job aÃºn no finaliza correctamente")

    result_path = job.get("result_path")
    if not result_path or not os.path.exists(result_path):
        raise HTTPException(status_code=404, detail="No se encontrÃ³ archivo de resultado")

    with open(result_path, "r", encoding="utf-8") as f:
        result_data = json.load(f)

    return {
        "ok": True,
        "job_id": job_id,
        "summary": job.get("summary", {}),
        "results": result_data,
    }


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
