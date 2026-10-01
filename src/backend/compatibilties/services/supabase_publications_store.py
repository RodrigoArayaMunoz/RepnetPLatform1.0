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

    @property
    def incremental_upsert_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return (
            f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/"
            "upsert_publicaciones_ml_incremental"
        )

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
                        "seller_id,mlc,sku,part_number,titulo,fecha_creacion,"
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
                    "select": "mlc,sku,part_number,titulo",
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

    async def upsert_incremental_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        payload_rows = [
            {key: value for key, value in row.items() if key != "sync_run_id"}
            for row in rows
        ]
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.incremental_upsert_url,
                headers=self._headers(),
                json={"publication_rows": payload_rows},
            )

            if response.status_code >= 400:
                logger.error(
                    "[SUPABASE_PUBLICATIONS][INCREMENTAL_UPSERT_ERROR] "
                    "status=%s rows=%s body=%s",
                    response.status_code,
                    len(payload_rows),
                    response.text[:1000],
                )
                raise RuntimeError(
                    "No se pudieron guardar las publicaciones notificadas por "
                    "Mercado Libre. Verifica que la migracion de upsert incremental "
                    f"este aplicada ({response.status_code})."
                )

            # A successful RPC response alone does not prove that its deployed
            # definition stored PART_NUMBER. Raise on a mismatch so the existing
            # notification task retries instead of marking the event processed.
            await self._verify_incremental_part_numbers(client, payload_rows)

        return len(payload_rows)

    async def _verify_incremental_part_numbers(
        self,
        client: httpx.AsyncClient,
        rows: list[dict[str, Any]],
    ) -> None:
        expected_by_seller: dict[str, dict[str, str]] = {}
        for row in rows:
            value = row.get("part_number")
            if value is None or not str(value).strip():
                # PART_NUMBER is optional; its absence in ML is not a failure.
                continue
            seller_id = str(row["seller_id"])
            expected_by_seller.setdefault(seller_id, {})[str(row["mlc"]).strip()] = (
                str(value).strip()
            )

        for seller_id, expected in expected_by_seller.items():
            item_ids = list(expected)
            for start in range(0, len(item_ids), 100):
                batch = item_ids[start : start + 100]
                response = await client.get(
                    self.table_url,
                    headers=self._headers(),
                    params={
                        "select": "mlc,part_number",
                        "seller_id": f"eq.{seller_id}",
                        "mlc": "in.(" + ",".join(json.dumps(item_id) for item_id in batch) + ")",
                        "limit": str(len(batch)),
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(
                        "No se pudo verificar part_number despues del guardado "
                        f"en publicaciones_ml ({response.status_code})."
                    )
                stored_rows = response.json()
                if not isinstance(stored_rows, list):
                    raise RuntimeError("Respuesta invalida al verificar part_number en Supabase.")
                stored = {
                    str(row.get("mlc") or ""): row.get("part_number")
                    for row in stored_rows
                    if isinstance(row, dict)
                }
                mismatches = [
                    item_id for item_id in batch
                    if stored.get(item_id) != expected[item_id]
                ]
                if mismatches:
                    raise RuntimeError(
                        "Supabase no guardo el PART_NUMBER recibido de Mercado Libre "
                        f"para {', '.join(mismatches)}. Verifica la funcion "
                        "upsert_publicaciones_ml_incremental y la migracion de part_number."
                    )

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

    async def delete_stale_rows(
        self,
        *,
        seller_id: str,
        sync_run_id: str,
        synchronized_before: str | None = None,
    ) -> None:
        params = {
            "seller_id": f"eq.{seller_id}",
            "sync_run_id": f"neq.{sync_run_id}",
        }
        if synchronized_before:
            params["sincronizado_at"] = f"lt.{synchronized_before}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.delete(
                self.table_url,
                headers=self._headers(),
                params=params,
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
