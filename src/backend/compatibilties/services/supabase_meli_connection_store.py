import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from config import settings
from services.token_store import token_store

logger = logging.getLogger(__name__)


class SupabaseMeliConnectionStore:
    def __init__(self) -> None:
        self.table_name = settings.supabase_meli_connection_table

    @property
    def table_url(self) -> str | None:
        if not settings.supabase_url:
            return None
        return f"{settings.supabase_url.rstrip('/')}/rest/v1/{self.table_name}"

    @property
    def can_read(self) -> bool:
        return bool(
            self.table_url
            and (settings.supabase_service_role_key or settings.supabase_anon_key)
        )

    @property
    def can_write(self) -> bool:
        return bool(self.table_url and settings.supabase_service_role_key)

    def _headers(
        self,
        *,
        writable: bool,
        return_representation: bool = False,
    ) -> dict[str, str] | None:
        api_key = (
            settings.supabase_service_role_key
            if writable
            else settings.supabase_service_role_key or settings.supabase_anon_key
        )
        if not api_key:
            return None

        headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        if return_representation:
            headers["Prefer"] = "return=representation"
        return headers

    async def _request(
        self,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        writable: bool,
        return_representation: bool = False,
    ) -> Any:
        if not self.table_url:
            return None

        headers = self._headers(
            writable=writable,
            return_representation=return_representation,
        )
        if not headers:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(
                    method=method,
                    url=self.table_url,
                    headers=headers,
                    params=params,
                    json=json_body,
                )

            if response.status_code >= 400:
                logger.warning(
                    "Supabase meli_global_connection %s fallo con status %s: %s",
                    method,
                    response.status_code,
                    response.text,
                )
                return None

            if not response.content:
                return None

            return response.json()
        except Exception as error:
            logger.warning(
                "No se pudo completar la sincronizacion con Supabase: %s",
                error,
            )
            return None

    async def list_rows(self, *, include_tokens: bool) -> list[dict[str, Any]]:
        if not self.can_read:
            return []

        if include_tokens and not settings.supabase_service_role_key:
            logger.warning(
                "No se pueden leer tokens desde Supabase sin SUPABASE_SERVICE_ROLE_KEY."
            )
            return []

        select_columns = "id,is_active,ml_user_id"
        if include_tokens:
            select_columns = (
                "id,is_active,ml_user_id,access_token,refresh_token,expires_at"
            )

        data = await self._request(
            "GET",
            params={
                "select": select_columns,
                "order": "id.asc",
            },
            writable=False,
        )

        return data if isinstance(data, list) else []

    @staticmethod
    def _serialize_expires_at(expires_at: Any) -> str | None:
        if expires_at in (None, ""):
            return None

        if isinstance(expires_at, (int, float)):
            return datetime.fromtimestamp(int(expires_at), tz=UTC).isoformat()

        return str(expires_at)

    @staticmethod
    def _parse_expires_at(expires_at: Any) -> int:
        if expires_at in (None, ""):
            return 0

        if isinstance(expires_at, (int, float)):
            return int(expires_at)

        text = str(expires_at).strip()
        if not text:
            return 0

        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return 0

    @classmethod
    def _build_payload(
        cls,
        token_data: dict[str, Any],
        *,
        is_active: bool,
    ) -> dict[str, Any]:
        return {
            "is_active": is_active,
            "ml_user_id": str(token_data.get("user_id") or ""),
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": cls._serialize_expires_at(token_data.get("expires_at")),
        }

    async def sync_connection(
        self,
        token_data: dict[str, Any],
        *,
        is_active: bool,
    ) -> bool:
        if not self.can_write:
            logger.warning(
                "No se puede escribir en Supabase. Configura SUPABASE_SERVICE_ROLE_KEY."
            )
            return False

        rows = await self.list_rows(include_tokens=False)
        payload = self._build_payload(token_data, is_active=is_active)

        if rows:
            primary_id = rows[0].get("id")
            if primary_id is None:
                return False

            updated = await self._request(
                "PATCH",
                params={"id": f"eq.{primary_id}"},
                json_body=payload,
                writable=True,
                return_representation=True,
            )

            if len(rows) > 1:
                await self._request(
                    "DELETE",
                    params={"id": f"neq.{primary_id}"},
                    writable=True,
                )

            return updated is not None

        created = await self._request(
            "POST",
            json_body=[payload],
            writable=True,
            return_representation=True,
        )
        return created is not None

    async def mark_disconnected(self, user_id: int | str | None = None) -> bool:
        if not self.can_write:
            return False

        rows = await self.list_rows(include_tokens=False)
        if not rows:
            return False

        primary_id = rows[0].get("id")
        if primary_id is None:
            return False

        payload: dict[str, Any] = {
            "is_active": False,
            "access_token": None,
            "refresh_token": None,
            "expires_at": None,
        }
        if user_id is not None:
            payload["ml_user_id"] = str(user_id)

        updated = await self._request(
            "PATCH",
            params={"id": f"eq.{primary_id}"},
            json_body=payload,
            writable=True,
            return_representation=True,
        )

        if len(rows) > 1:
            await self._request(
                "DELETE",
                params={"id": f"neq.{primary_id}"},
                writable=True,
            )

        return updated is not None

    async def restore_token_store(self) -> bool:
        if not self.can_read:
            return False

        rows = await self.list_rows(include_tokens=True)
        if not rows:
            return False

        row = rows[0]
        if len(rows) > 1 and self.can_write and row.get("id") is not None:
            await self._request(
                "DELETE",
                params={"id": f"neq.{row.get('id')}"},
                writable=True,
            )

        if not row.get("is_active"):
            return False

        ml_user_id = row.get("ml_user_id")
        access_token = row.get("access_token")
        refresh_token = row.get("refresh_token")
        expires_at = self._parse_expires_at(row.get("expires_at"))

        if not ml_user_id or not access_token or not refresh_token:
            return False

        now = int(datetime.now(tz=UTC).timestamp())
        expires_in = max(expires_at - now, 0) if expires_at else 0

        token_store.set(
            ml_user_id,
            {
                "user_id": str(ml_user_id),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "scope": None,
                "expires_in": expires_in,
                "expires_at": expires_at,
            },
        )
        return True


supabase_meli_connection_store = SupabaseMeliConnectionStore()
