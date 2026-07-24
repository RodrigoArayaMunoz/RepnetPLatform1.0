from fastapi import APIRouter, HTTPException

from services.publication_sync_store import publication_sync_store
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.supabase_publications_store import supabase_publications_store
from tasks.publication_sync_tasks import sync_publications_task

router = APIRouter(prefix="/publications", tags=["publications"])


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
