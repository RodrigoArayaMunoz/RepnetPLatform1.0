import logging
import os
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SupabaseProcessStore:
    def __init__(self) -> None:
        self.table_name = settings.supabase_process_table

    @property
    def table_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return f"{settings.supabase_url.rstrip('/')}/rest/v1/{self.table_name}"

    @property
    def storage_base_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return f"{settings.supabase_url.rstrip('/')}/storage/v1/object"

    @property
    def can_access(self) -> bool:
        return bool(
            self.table_url
            and self.storage_base_url
            and settings.supabase_service_role_key
        )

    def _headers(self, *, return_representation: bool = False) -> dict[str, str]:
        api_key = settings.supabase_service_role_key
        if not api_key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY no configurado")

        headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        if return_representation:
            headers["Prefer"] = "return=representation"
        return headers

    async def _table_request(
        self,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        return_representation: bool = False,
    ) -> Any:
        if not self.table_url:
            raise RuntimeError("SUPABASE_URL no configurado")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=self.table_url,
                headers=self._headers(return_representation=return_representation),
                params=params,
                json=json_body,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase procesos {method} fallo con status {response.status_code}: {response.text}"
            )

        if not response.content:
            return None

        return response.json()

    async def list_pending_processes(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.can_access:
            raise RuntimeError("Supabase no esta configurado para cola de procesos")

        params = {
            "select": "id,proceso_id,archivo,fecha_proceso,hora_proceso,estado,storage_bucket,storage_path,generado",
            "estado": "eq.Pendiente",
            "order": "fecha_proceso.desc,hora_proceso.desc,id.desc",
        }
        if limit is not None:
            params["limit"] = str(limit)

        data = await self._table_request("GET", params=params)
        return data if isinstance(data, list) else []

    async def update_process_status(self, row_id: int | str, status: str) -> None:
        await self._table_request(
            "PATCH",
            params={"id": f"eq.{row_id}"},
            json_body={"estado": status},
            return_representation=True,
        )

    async def download_process_file(
        self,
        *,
        bucket_name: str,
        storage_path: str,
        original_filename: str,
    ) -> str:
        if not self.storage_base_url:
            raise RuntimeError("Supabase storage no configurado")

        encoded_path = quote(storage_path, safe="/")
        url = f"{self.storage_base_url}/{bucket_name}/{encoded_path}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(
                url,
                headers=self._headers(),
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"No se pudo descargar {bucket_name}/{storage_path}: {response.status_code} {response.text}"
            )

        downloads_dir = os.path.join(settings.upload_dir, "process_queue")
        os.makedirs(downloads_dir, exist_ok=True)

        safe_filename = os.path.basename(original_filename or storage_path or "proceso.xlsx")
        local_filename = f"{uuid.uuid4()}_{safe_filename}"
        local_path = os.path.join(downloads_dir, local_filename)

        with open(local_path, "wb") as file_handle:
            file_handle.write(response.content)

        return local_path


supabase_process_store = SupabaseProcessStore()
