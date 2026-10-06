import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SupabaseRefaxConnectionStore:
    ENVIRONMENT_IDS = {"production": 1, "test": 2}

    def __init__(self, environment: str = "production") -> None:
        if environment not in self.ENVIRONMENT_IDS:
            raise ValueError("Ambiente REFAX no valido")
        self.environment = environment
        self.connection_id = self.ENVIRONMENT_IDS[environment]
        self.table_name = settings.supabase_refax_connection_table

    @property
    def table_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return f"{settings.supabase_url.rstrip('/')}/rest/v1/{self.table_name}"

    @property
    def can_access(self) -> bool:
        return bool(self.table_url and settings.supabase_service_role_key)

    def _headers(
        self,
        *,
        return_representation: bool = False,
        upsert: bool = False,
    ) -> dict[str, str]:
        service_key = settings.supabase_service_role_key
        if not service_key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY no configurado")

        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        preferences = []
        if return_representation:
            preferences.append("return=representation")
        if upsert:
            preferences.append("resolution=merge-duplicates")
        if preferences:
            headers["Prefer"] = ",".join(preferences)
        return headers

    async def _request(
        self,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        return_representation: bool = False,
        upsert: bool = False,
    ) -> Any:
        if not self.table_url or not self.can_access:
            raise RuntimeError(
                "Persistencia REFAX no configurada en Supabase"
            )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(
                    method,
                    self.table_url,
                    headers=self._headers(
                        return_representation=return_representation,
                        upsert=upsert,
                    ),
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as error:
            raise RuntimeError(
                "No se pudo acceder a la persistencia REFAX"
            ) from error

        if response.status_code >= 400:
            logger.error(
                "Supabase REFAX %s fallo con status %s",
                method,
                response.status_code,
            )
            raise RuntimeError(
                "Supabase no pudo guardar el estado de REFAX; aplica la migracion pendiente"
            )

        if not response.content:
            return None
        return response.json()

    async def get(self) -> dict[str, Any] | None:
        data = await self._request(
            "GET",
            params={
                "id": f"eq.{self.connection_id}",
                "environment": f"eq.{self.environment}",
                "select": (
                    "id,is_active,access_token,obtained_at,refresh_at,"
                    "expires_at,last_error,updated_at"
                ),
                "limit": "1",
            },
        )
        if isinstance(data, list) and data:
            return data[0]
        return None

    async def save_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._request(
            "POST",
            params={"on_conflict": "id"},
            json_body={
                "id": self.connection_id,
                "environment": self.environment,
                "is_active": True,
                "access_token": payload["access_token"],
                "obtained_at": payload["obtained_at"],
                "refresh_at": payload["refresh_at"],
                "expires_at": payload["expires_at"],
                "last_error": None,
            },
            return_representation=True,
            upsert=True,
        )
        if not isinstance(data, list) or not data:
            raise RuntimeError("Supabase no devolvio el estado REFAX guardado")
        return data[0]

    async def record_failure(
        self,
        message: str,
        *,
        deactivate: bool,
    ) -> None:
        existing = await self.get()
        if existing:
            update: dict[str, Any] = {"last_error": message[:1000]}
            if deactivate:
                update["is_active"] = False
            await self._request(
                "PATCH",
                params={
                    "id": f"eq.{self.connection_id}",
                    "environment": f"eq.{self.environment}",
                },
                json_body=update,
            )
            return

        await self._request(
            "POST",
            params={"on_conflict": "id"},
            json_body={
                "id": self.connection_id,
                "environment": self.environment,
                "is_active": False,
                "last_error": message[:1000],
            },
            upsert=True,
        )


supabase_refax_connection_store = SupabaseRefaxConnectionStore()
supabase_refax_test_connection_store = SupabaseRefaxConnectionStore("test")
