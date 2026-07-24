import json
import logging
from typing import Any

import redis.asyncio as redis

from config import settings

logger = logging.getLogger(__name__)


class PublicationDescriptionCache:
    CACHE_VERSION = 1

    def __init__(self, redis_url: str | None = None) -> None:
        self.client = redis.from_url(
            redis_url or settings.redis_url,
            decode_responses=True,
        )

    @staticmethod
    def _key(item_id: str) -> str:
        return f"publication-description:v1:{item_id.strip().upper()}"

    async def get(self, item_id: str) -> tuple[str, str] | None:
        if not item_id:
            return None

        key = self._key(item_id)
        raw = await self.client.get(key)
        if not raw:
            return None

        try:
            payload: Any = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            await self.client.delete(key)
            return None

        if not isinstance(payload, dict):
            await self.client.delete(key)
            return None

        if payload.get("version") != self.CACHE_VERSION:
            await self.client.delete(key)
            return None

        result = str(payload.get("result") or "")
        if result not in {"found", "missing"}:
            await self.client.delete(key)
            return None

        return str(payload.get("description") or ""), result

    async def set(
        self,
        item_id: str,
        *,
        description: str,
        result: str,
    ) -> None:
        if not item_id or result not in {"found", "missing"}:
            return

        ttl_seconds = (
            settings.ml_publication_description_cache_ttl_seconds
            if result == "found"
            else settings.ml_publication_missing_cache_ttl_seconds
        )
        payload = {
            "version": self.CACHE_VERSION,
            "description": description,
            "result": result,
        }
        await self.client.set(
            self._key(item_id),
            json.dumps(payload, ensure_ascii=False),
            ex=int(ttl_seconds),
        )

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            logger.exception(
                "[PUBLICATION_DESCRIPTION_CACHE][CLOSE_ERROR]"
            )
