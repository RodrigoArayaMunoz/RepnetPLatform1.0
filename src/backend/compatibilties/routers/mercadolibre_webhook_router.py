"""Acknowledge late callbacks for the retired Mercado Libre integration.

This endpoint deliberately does not parse payloads, enqueue jobs, or contact
Mercado Libre, Redis, or Supabase. HTTP 200 stops retries from the old webhook.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks", include_in_schema=False)


@router.get("/mercadolibre/health")
async def mercadolibre_webhook_health() -> dict[str, bool]:
    return {"ok": True, "enabled": False}


@router.post("/mercadolibre")
async def receive_mercadolibre_notification() -> dict[str, bool]:
    return {"ok": True, "queued": False, "disabled": True}
