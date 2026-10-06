import asyncio
import json
import logging
import re
from collections.abc import Callable
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
                        "sync_run_id,sincronizado_at,status,has_compatibilities"
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
        return await self._count_rows(creation_date=creation_date, seller_id=seller_id)

    async def count_all(self) -> int:
        return await self._count_rows()

    async def _count_rows(
        self,
        *,
        creation_date: str | None = None,
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

        params = {"select": "mlc"}
        if creation_date:
            params["fecha_creacion"] = f"eq.{creation_date}"
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
                "No se pudieron contar las publicaciones "
                f"({response.status_code})."
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
        return await self._list_rows(creation_date=creation_date, seller_id=seller_id)

    async def list_all(
        self,
        *,
        on_page: Callable[[int, int | None], None] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._list_rows(on_page=on_page)

    async def _list_rows(
        self,
        *,
        creation_date: str | None = None,
        seller_id: str | None = None,
        on_page: Callable[[int, int | None], None] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        total_rows: int | None = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            start = 0
            while True:
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
                    "select": "mlc,sku,part_number,titulo,status,has_compatibilities",
                    "order": "seller_id.asc,mlc.asc",
                }
                if start == 0:
                    headers["Prefer"] = "count=exact"
                if creation_date:
                    params["fecha_creacion"] = f"eq.{creation_date}"
                if seller_id:
                    params["seller_id"] = f"eq.{seller_id}"

                response = await client.get(
                    self.table_url,
                    headers=headers,
                    params=params,
                )

                match = re.search(r"/(\d+)$", response.headers.get("content-range", ""))
                if match:
                    total_rows = int(match.group(1))
                if response.status_code == 416 and total_rows is not None and start >= total_rows:
                    break
                if response.status_code >= 400:
                    logger.error(
                        "[SUPABASE_PUBLICATIONS][LIST_DATE_ERROR] "
                        "status=%s body=%s",
                        response.status_code,
                        response.text[:1000],
                    )
                    raise RuntimeError(
                        "No se pudieron consultar las publicaciones "
                        f"({response.status_code})."
                    )

                batch = response.json()
                if not isinstance(batch, list) or any(
                    not isinstance(item, dict) for item in batch
                ):
                    raise RuntimeError(
                        "Supabase devolvio una respuesta invalida al filtrar "
                        "publicaciones."
                    )

                if not batch:
                    break
                rows.extend(batch)
                start += len(batch)
                if on_page is not None:
                    on_page(start, total_rows)
                # Respect the actual server page size, including lower limits.
                if total_rows is not None and start >= total_rows:
                    break
                if total_rows is None and creation_date is not None and len(batch) < self.READ_PAGE_SIZE:
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

            # An older deployed RPC may silently ignore new JSON fields. Check
            # persistence before allowing the notification to be marked processed.
            await self._verify_incremental_fields(client, payload_rows)

        return len(payload_rows)

    async def _verify_incremental_fields(
        self,
        client: httpx.AsyncClient,
        rows: list[dict[str, Any]],
    ) -> None:
        fields = ("part_number", "status", "has_compatibilities")
        expected_by_seller: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            seller_id = str(row["seller_id"])
            item_id = str(row["mlc"]).strip()
            expected = expected_by_seller.setdefault(seller_id, {}).setdefault(item_id, {})
            for field in fields:
                value = row.get(field)
                if value is None:
                    continue
                if field != "has_compatibilities":
                    value = str(value).strip()
                    if not value:
                        continue
                # False is a confirmed value and must also be verified.
                expected[field] = value

        for seller_id, expected in expected_by_seller.items():
            item_ids = [item_id for item_id, values in expected.items() if values]
            for start in range(0, len(item_ids), 100):
                batch = item_ids[start : start + 100]
                response = await client.get(
                    self.table_url,
                    headers=self._headers(),
                    params={
                        "select": "mlc,part_number,status,has_compatibilities",
                        "seller_id": f"eq.{seller_id}",
                        "mlc": "in.(" + ",".join(json.dumps(item_id) for item_id in batch) + ")",
                        "limit": str(len(batch)),
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(
                        "No se pudo verificar part_number/status/has_compatibilities despues del guardado "
                        f"en publicaciones_ml ({response.status_code})."
                    )
                stored_rows = response.json()
                if not isinstance(stored_rows, list):
                    raise RuntimeError("Respuesta invalida al verificar campos de publicaciones en Supabase.")
                stored = {
                    str(row.get("mlc") or ""): row
                    for row in stored_rows
                    if isinstance(row, dict)
                }
                for field in fields:
                    mismatches = [
                        item_id for item_id in batch
                        if field in expected[item_id]
                        and stored.get(item_id, {}).get(field) != expected[item_id][field]
                    ]
                    if mismatches:
                        label = "PART_NUMBER" if field == "part_number" else field
                        raise RuntimeError(
                            f"Supabase no guardo el {label} recibido de Mercado Libre "
                            f"para {', '.join(mismatches)}. Verifica la funcion "
                            "upsert_publicaciones_ml_incremental y las migraciones de publicaciones."
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
