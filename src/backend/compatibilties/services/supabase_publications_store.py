import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SupabasePublicationsStore:
    UPSERT_BATCH_SIZE = 500

    def __init__(self) -> None:
        self.table_name = settings.supabase_publications_table

    @property
    def table_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return f"{settings.supabase_url.rstrip('/')}/rest/v1/{self.table_name}"

    def _headers(self, *, upsert: bool = False) -> dict[str, str]:
        service_key = settings.supabase_service_role_key
        if not self.table_url or not service_key:
            raise RuntimeError(
                "Supabase no está configurado para guardar publicaciones. "
                "Revisa SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY."
            )

        prefer = "return=minimal"
        if upsert:
            prefer = "resolution=merge-duplicates,return=minimal"

        return {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    async def ensure_ready(self) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.table_url,
                headers=self._headers(),
                params={
                    "select": (
                        "seller_id,mlc,sku,titulo,fecha_creacion,"
                        "sync_run_id,sincronizado_at"
                    ),
                    "limit": "1",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][SCHEMA_ERROR] status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                "La tabla publicaciones_ml no existe o no tiene la estructura "
                "requerida. Ejecuta primero la migración SQL incluida."
            )

    async def has_rows(self) -> bool:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.table_url,
                headers=self._headers(),
                params={
                    "select": "mlc",
                    "limit": "1",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][EXISTS_ERROR] status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                "No se pudo verificar si existen publicaciones guardadas "
                f"({response.status_code})."
            )

        rows = response.json()
        return isinstance(rows, list) and bool(rows)

    async def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.table_url,
                headers=self._headers(upsert=True),
                params={"on_conflict": "seller_id,mlc"},
                json=rows,
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][UPSERT_ERROR] status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                f"Supabase rechazó el lote de publicaciones ({response.status_code}). "
                "Verifica que la tabla publicaciones_ml tenga la estructura indicada."
            )

        return len(rows)

    async def delete_stale_rows(self, *, seller_id: str, sync_run_id: str) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.delete(
                self.table_url,
                headers=self._headers(),
                params={
                    "seller_id": f"eq.{seller_id}",
                    "sync_run_id": f"neq.{sync_run_id}",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][DELETE_STALE_ERROR] status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                f"No se pudieron depurar publicaciones obsoletas ({response.status_code})."
            )


supabase_publications_store = SupabasePublicationsStore()
