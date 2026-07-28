import asyncio
import json
import logging
import re
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SupabasePublicationsStore:
    UPSERT_BATCH_SIZE = 500
    UPSERT_MAX_ATTEMPTS = 5
    UPSERT_RETRY_BASE_DELAY_SECONDS = 1.0
    UPSERT_RETRY_MAX_DELAY_SECONDS = 8.0
    UPSERT_SPLIT_MIN_BATCH_SIZE = 50
    READ_PAGE_SIZE = 1000
    _RETRYABLE_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
        520,
        522,
        524,
    }
    _SPLITTABLE_STATUS_CODES = {
        413,
        500,
        502,
        503,
        504,
        520,
        522,
        524,
    }

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

    async def count_by_creation_date(
        self,
        creation_date: str,
        *,
        seller_id: str | None = None,
    ) -> int:
        headers = self._headers()
        headers.update(
            {
                "Prefer": "count=exact",
                "Range": "0-0",
                "Range-Unit": "items",
            }
        )

        params = {
            "select": "mlc",
            "fecha_creacion": f"eq.{creation_date}",
        }
        if seller_id:
            params["seller_id"] = f"eq.{seller_id}"

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.table_url,
                headers=headers,
                params=params,
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][COUNT_DATE_ERROR] status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError(
                "No se pudieron contar las publicaciones de la fecha "
                f"{creation_date} ({response.status_code})."
            )

        content_range = response.headers.get("content-range", "")
        match = re.search(r"/(\d+)$", content_range)
        return int(match.group(1)) if match else 0

    async def count_by_sync_run(
        self,
        *,
        seller_id: str,
        sync_run_id: str,
    ) -> int:
        headers = self._headers()
        headers.update(
            {
                "Prefer": "count=exact",
                "Range": "0-0",
                "Range-Unit": "items",
            }
        )

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self.table_url,
                headers=headers,
                params={
                    "select": "mlc",
                    "seller_id": f"eq.{seller_id}",
                    "sync_run_id": f"eq.{sync_run_id}",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "[SUPABASE_PUBLICATIONS][COUNT_RUN_ERROR] "
                "status=%s seller_id=%s sync_run_id=%s body=%s",
                response.status_code,
                seller_id,
                sync_run_id,
                response.text[:1000],
            )
            raise RuntimeError(
                "No se pudo verificar la cantidad guardada de la carga "
                f"{sync_run_id} ({response.status_code})."
            )

        content_range = response.headers.get("content-range", "")
        match = re.search(r"/(\d+)$", content_range)
        return int(match.group(1)) if match else 0

    async def list_by_creation_date(
        self,
        creation_date: str,
        *,
        seller_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            for start in range(0, 1_000_000, self.READ_PAGE_SIZE):
                headers = self._headers()
                headers.update(
                    {
                        "Range": (
                            f"{start}-{start + self.READ_PAGE_SIZE - 1}"
                        ),
                        "Range-Unit": "items",
                    }
                )
                params = {
                    "select": "mlc,sku,titulo",
                    "fecha_creacion": f"eq.{creation_date}",
                    "order": "mlc.asc",
                }
                if seller_id:
                    params["seller_id"] = f"eq.{seller_id}"

                response = await client.get(
                    self.table_url,
                    headers=headers,
                    params=params,
                )

                if response.status_code >= 400:
                    logger.error(
                        "[SUPABASE_PUBLICATIONS][LIST_DATE_ERROR] "
                        "status=%s body=%s",
                        response.status_code,
                        response.text[:1000],
                    )
                    raise RuntimeError(
                        "No se pudieron consultar las publicaciones de la fecha "
                        f"{creation_date} ({response.status_code})."
                    )

                batch = response.json()
                if not isinstance(batch, list):
                    raise RuntimeError(
                        "Supabase devolvio una respuesta invalida al filtrar "
                        "publicaciones."
                    )

                rows.extend(item for item in batch if isinstance(item, dict))
                if len(batch) < self.READ_PAGE_SIZE:
                    break

        return rows

    async def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._upsert_batch(client, rows)

    async def _upsert_batch(
        self,
        client: httpx.AsyncClient,
        rows: list[dict[str, Any]],
    ) -> int:
        last_status: int | None = None
        last_body = ""

        for attempt in range(1, self.UPSERT_MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    self.table_url,
                    headers=self._headers(upsert=True),
                    params={"on_conflict": "seller_id,mlc"},
                    json=rows,
                )
            except httpx.TransportError as exc:
                logger.warning(
                    "[SUPABASE_PUBLICATIONS][UPSERT_TRANSPORT_RETRY] "
                    "attempt=%s/%s rows=%s error=%s",
                    attempt,
                    self.UPSERT_MAX_ATTEMPTS,
                    len(rows),
                    type(exc).__name__,
                )
                if attempt >= self.UPSERT_MAX_ATTEMPTS:
                    raise RuntimeError(
                        "No se pudo conectar con Supabase después de varios intentos. "
                        "La carga guardada hasta ahora se conserva; vuelve a ejecutarla."
                    ) from exc

                await asyncio.sleep(self._retry_delay(attempt))
                continue

            if response.status_code < 400:
                return len(rows)

            last_status = response.status_code
            last_body = response.text[:1000]
            can_retry = last_status in self._RETRYABLE_STATUS_CODES

            if can_retry and attempt < self.UPSERT_MAX_ATTEMPTS:
                logger.warning(
                    "[SUPABASE_PUBLICATIONS][UPSERT_RETRY] "
                    "status=%s attempt=%s/%s rows=%s body=%s",
                    last_status,
                    attempt,
                    self.UPSERT_MAX_ATTEMPTS,
                    len(rows),
                    last_body,
                )
                await asyncio.sleep(self._retry_delay(attempt))
                continue

            break

        if (
            last_status in self._SPLITTABLE_STATUS_CODES
            and len(rows) > self.UPSERT_SPLIT_MIN_BATCH_SIZE
        ):
            midpoint = len(rows) // 2
            logger.warning(
                "[SUPABASE_PUBLICATIONS][UPSERT_SPLIT] "
                "status=%s rows=%s left=%s right=%s",
                last_status,
                len(rows),
                midpoint,
                len(rows) - midpoint,
            )
            left_count = await self._upsert_batch(client, rows[:midpoint])
            right_count = await self._upsert_batch(client, rows[midpoint:])
            return left_count + right_count

        logger.error(
            "[SUPABASE_PUBLICATIONS][UPSERT_ERROR] status=%s rows=%s body=%s",
            last_status,
            len(rows),
            last_body,
        )
        raise RuntimeError(self._upsert_error_message(last_status, last_body))

    def _retry_delay(self, attempt: int) -> float:
        return min(
            self.UPSERT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
            self.UPSERT_RETRY_MAX_DELAY_SECONDS,
        )

    @staticmethod
    def _response_detail(body: str) -> str:
        if not body:
            return ""

        try:
            parsed = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            return body.strip()[:300]

        if not isinstance(parsed, dict):
            return body.strip()[:300]

        parts = [
            str(parsed.get(key) or "").strip()
            for key in ("message", "details", "hint", "code")
        ]
        return " ".join(part for part in parts if part)[:300]

    def _upsert_error_message(self, status: int | None, body: str) -> str:
        if status in {400, 404}:
            detail = self._response_detail(body)
            suffix = f" Detalle: {detail}" if detail else ""
            return (
                f"Supabase rechazó los datos del lote ({status}). Verifica la "
                f"estructura de la tabla publicaciones_ml.{suffix}"
            )

        if status in {401, 403}:
            return (
                f"Supabase rechazó las credenciales del backend ({status}). "
                "Revisa SUPABASE_SERVICE_ROLE_KEY."
            )

        if status in self._RETRYABLE_STATUS_CODES:
            return (
                f"Supabase no pudo procesar un lote después de "
                f"{self.UPSERT_MAX_ATTEMPTS} intentos ({status}). Es un error "
                "temporal del servicio; la carga guardada se conserva y puede "
                "volver a ejecutarse."
            )

        return f"Supabase rechazó el lote de publicaciones ({status})."

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
