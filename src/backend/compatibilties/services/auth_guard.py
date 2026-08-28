import hashlib
import time
from typing import Any

import httpx
from fastapi import HTTPException, Request

from config import settings


class SupabaseAuthVerifier:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def _token_cache_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise HTTPException(
                status_code=401,
                detail="Falta header Authorization",
            )

        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=401,
                detail="Header Authorization invalido",
            )

        return token.strip()

    async def verify_authorization_header(
        self,
        authorization_header: str | None,
    ) -> dict[str, Any]:
        token = self._extract_bearer_token(authorization_header)

        now = time.time()
        cache_key = self._token_cache_key(token)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        if not settings.supabase_url or not settings.supabase_anon_key:
            raise HTTPException(
                status_code=503,
                detail="Autenticacion no configurada en backend",
            )

        user_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(user_url, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(
                status_code=503,
                detail="No se pudo verificar la sesion",
            )

        if response.status_code in (401, 403):
            raise HTTPException(
                status_code=401,
                detail="Sesion invalida o expirada",
            )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=503,
                detail="Supabase no pudo verificar la sesion",
            )

        user = response.json()
        if not isinstance(user, dict) or not user.get("id"):
            raise HTTPException(
                status_code=401,
                detail="Sesion invalida",
            )

        ttl = max(int(settings.backend_auth_cache_ttl_seconds), 0)
        if ttl:
            self._cache[cache_key] = (now + ttl, user)

        return user


supabase_auth_verifier = SupabaseAuthVerifier()


def is_public_path(path: str) -> bool:
    public_paths = {
        "/auth/callback",
        "/webhooks/mercadolibre",
        "/webhooks/mercadolibre/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
    return path in public_paths or path.startswith("/docs/")


async def verify_supabase_request(request: Request) -> dict[str, Any]:
    return await supabase_auth_verifier.verify_authorization_header(
        request.headers.get("authorization")
    )
