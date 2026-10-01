import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from config import settings
from services.supabase_refax_connection_store import (
    SupabaseRefaxConnectionStore,
    supabase_refax_connection_store,
)

logger = logging.getLogger(__name__)


class RefaxConnectionError(RuntimeError):
    pass


class RefaxConnectionService:
    TOKEN_PATH = "/api/Autenticacion/GetToken"

    def __init__(
        self,
        store: SupabaseRefaxConnectionStore = supabase_refax_connection_store,
    ) -> None:
        self.store = store
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _configuration_error() -> str | None:
        if not settings.refax_provider_code or not settings.refax_api_key:
            return "Configura REFAX_PROVIDER_CODE y REFAX_API_KEY en el backend"
        if (
            settings.refax_token_refresh_after_seconds
            >= settings.refax_token_lifetime_seconds
        ):
            return "REFAX_TOKEN_REFRESH_AFTER_SECONDS debe ser menor a la duracion del token"
        return None

    async def _request_token(self) -> str:
        configuration_error = self._configuration_error()
        if configuration_error:
            raise RefaxConnectionError(configuration_error)

        url = f"{settings.refax_api_base_url.rstrip('/')}{self.TOKEN_PATH}"
        payload = {
            "codigo": settings.refax_provider_code,
            "clave": settings.refax_api_key,
            "pais": settings.refax_country_code,
        }

        try:
            async with httpx.AsyncClient(
                timeout=settings.refax_http_timeout_seconds
            ) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as error:
            raise RefaxConnectionError(
                "No fue posible comunicarse con REFAX"
            ) from error

        if response.status_code >= 400:
            raise RefaxConnectionError(
                f"REFAX rechazo la autenticacion (HTTP {response.status_code})"
            )

        try:
            data = response.json()
        except ValueError as error:
            raise RefaxConnectionError(
                "REFAX devolvio una respuesta no valida"
            ) from error

        token = data.get("token") if isinstance(data, dict) else None
        status = (
            str(data.get("status") or "").strip().upper()
            if isinstance(data, dict)
            else ""
        )
        if not isinstance(token, str) or not token.strip() or status != "OK":
            raise RefaxConnectionError(
                "REFAX no entrego un token de acceso valido"
            )
        return token.strip()

    async def _authenticate(self) -> dict[str, Any]:
        token = await self._request_token()
        obtained_at = self._now()
        refresh_at = obtained_at + timedelta(
            seconds=settings.refax_token_refresh_after_seconds
        )
        expires_at = obtained_at + timedelta(
            seconds=settings.refax_token_lifetime_seconds
        )
        return await self.store.save_token(
            {
                "access_token": token,
                "obtained_at": obtained_at.isoformat(),
                "refresh_at": refresh_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        )

    async def connect(self) -> dict[str, Any]:
        async with self._refresh_lock:
            try:
                row = await self._authenticate()
            except RefaxConnectionError as error:
                with suppress(RuntimeError):
                    await self.store.record_failure(str(error), deactivate=True)
                raise
            except RuntimeError as error:
                raise RefaxConnectionError(str(error)) from error
        return self._public_state(row)

    def _is_connected(self, row: dict[str, Any] | None) -> bool:
        if not row or not row.get("is_active") or not row.get("access_token"):
            return False
        expires_at = self._parse_datetime(row.get("expires_at"))
        return bool(expires_at and expires_at > self._now())

    def _is_refresh_due(self, row: dict[str, Any]) -> bool:
        refresh_at = self._parse_datetime(row.get("refresh_at"))
        return not refresh_at or refresh_at <= self._now()

    def _public_state(self, row: dict[str, Any] | None) -> dict[str, Any]:
        connected = self._is_connected(row)
        return {
            "connected": connected,
            "refresh_at": row.get("refresh_at") if connected and row else None,
            "expires_at": row.get("expires_at") if connected and row else None,
            "last_error": row.get("last_error") if row else None,
        }

    async def _refresh_if_due(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._is_refresh_due(row):
            return row

        async with self._refresh_lock:
            current = await self.store.get()
            if not current or not current.get("is_active"):
                return current or row
            if not self._is_refresh_due(current):
                return current

            try:
                return await self._authenticate()
            except (RefaxConnectionError, RuntimeError) as error:
                logger.warning("No se pudo renovar el token REFAX: %s", error)
                expired = not self._is_connected(current)
                with suppress(RuntimeError):
                    await self.store.record_failure(
                        str(error),
                        deactivate=expired,
                    )
                current["last_error"] = str(error)
                if expired:
                    current["is_active"] = False
                return current

    async def status(self) -> dict[str, Any]:
        try:
            row = await self.store.get()
            if row and row.get("is_active") and self._is_refresh_due(row):
                row = await self._refresh_if_due(row)
        except RuntimeError as error:
            raise RefaxConnectionError(str(error)) from error
        return self._public_state(row)

    async def get_valid_token(self) -> str:
        try:
            row = await self.store.get()
            if row and row.get("is_active") and self._is_refresh_due(row):
                row = await self._refresh_if_due(row)
        except RuntimeError as error:
            raise RefaxConnectionError(str(error)) from error
        if not self._is_connected(row):
            raise RefaxConnectionError("REFAX no esta conectado")
        return str(row["access_token"])

    async def _refresh_loop(self) -> None:
        interval = settings.refax_refresh_check_interval_seconds
        while True:
            try:
                await asyncio.sleep(interval)
                row = await self.store.get()
                if row and row.get("is_active") and self._is_refresh_due(row):
                    await self._refresh_if_due(row)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Revision automatica de REFAX fallo: %s", error)

    async def startup(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return
        if not self.store.can_access or self._configuration_error():
            logger.warning(
                "Renovacion automatica REFAX inactiva: revisa credenciales y Supabase"
            )
            return
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(),
            name="refax-token-refresh",
        )

    async def shutdown(self) -> None:
        if not self._refresh_task:
            return
        self._refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._refresh_task
        self._refresh_task = None


refax_connection_service = RefaxConnectionService()
