import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from config import settings
from tasks.meli_sales_tasks import process_meli_notification_task

router = APIRouter(prefix="/webhooks", tags=["mercadolibre-webhooks"])

RESOURCE_PATTERNS = {
    "orders_v2": re.compile(r"^/orders/(?P<resource_id>\d+)$"),
    "shipments": re.compile(r"^/shipments/(?P<resource_id>\d+)$"),
}


def validate_meli_notification(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.ml_notifications_enabled:
        raise HTTPException(
            status_code=503,
            detail="Las notificaciones de Mercado Libre estan deshabilitadas.",
        )

    expected_application_id = (
        settings.ml_notification_application_id or settings.ml_client_id
    )
    if not expected_application_id:
        raise HTTPException(
            status_code=503,
            detail="El application_id de Mercado Libre no esta configurado.",
        )

    application_id = str(payload.get("application_id") or "").strip()
    if application_id != str(expected_application_id).strip():
        raise HTTPException(status_code=403, detail="application_id invalido.")

    user_id = str(payload.get("user_id") or "").strip()
    if not user_id.isdigit():
        raise HTTPException(status_code=400, detail="user_id invalido.")
    allowed_user_id = str(settings.ml_notification_allowed_user_id or "").strip()
    if allowed_user_id and user_id != allowed_user_id:
        raise HTTPException(status_code=403, detail="user_id no autorizado.")

    topic = str(payload.get("topic") or "").strip()
    pattern = RESOURCE_PATTERNS.get(topic)
    if pattern is None:
        raise HTTPException(status_code=400, detail="Topico no soportado.")

    resource = str(payload.get("resource") or "").strip()
    if not pattern.fullmatch(resource):
        raise HTTPException(status_code=400, detail="Resource invalido para el topico.")

    attempts = payload.get("attempts", 1)
    try:
        normalized_attempts = max(int(attempts), 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="attempts invalido.")

    return {
        **payload,
        "application_id": application_id,
        "user_id": user_id,
        "topic": topic,
        "resource": resource,
        "attempts": normalized_attempts,
    }


def notification_event_key(payload: dict[str, Any]) -> str:
    notification_id = str(payload.get("_id") or payload.get("id") or "").strip()
    if notification_id:
        return f"meli:{notification_id}"

    identity = {
        "application_id": payload.get("application_id"),
        "user_id": payload.get("user_id"),
        "topic": payload.get("topic"),
        "resource": payload.get("resource"),
        "sent": payload.get("sent"),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"meli:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@router.get("/mercadolibre/health")
async def mercadolibre_webhook_health() -> dict[str, bool]:
    return {"ok": True}


@router.post("/mercadolibre")
async def receive_mercadolibre_notification(
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = validate_meli_notification(payload)
    event_key = notification_event_key(normalized)
    try:
        task = process_meli_notification_task.apply_async(
            args=[normalized, event_key],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo encolar la notificacion.",
        ) from exc

    return {
        "ok": True,
        "queued": True,
        "event_key": event_key,
        "task_id": task.id,
    }
