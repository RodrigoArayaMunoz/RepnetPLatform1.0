import json
from typing import Any

import redis
from config import settings


class ProductCacheService:
    _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _ttl_seconds = 30 * 24 * 60 * 60  # 30 días

    @classmethod
    def _normalize(cls, value: Any) -> str:
        return "" if value is None else str(value).strip().lower()

    @classmethod
    def build_vehicle_key(
        cls,
        *,
        site_id: str,
        brand_name: str,
        model_name: str,
        year: int | None,
        version_name: str,
        engine_name: str,
        transmission_name: str,
    ) -> str:
        return ":".join(
            [
                "compat",
                "product",
                cls._normalize(site_id),
                cls._normalize(brand_name),
                cls._normalize(model_name),
                cls._normalize(year),
                cls._normalize(version_name),
                cls._normalize(engine_name),
                cls._normalize(transmission_name),
            ]
        )

    @classmethod
    def get_product_resolution(cls, key: str) -> dict | None:
        raw = cls._client.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @classmethod
    def set_product_resolution(cls, key: str, value: dict) -> None:
        cls._client.set(
            key,
            json.dumps(value, ensure_ascii=False),
            ex=cls._ttl_seconds,
        )

    @classmethod
    def get_item_compact(cls, item_id: str) -> dict | None:
        raw = cls._client.get(f"compat:item:{item_id}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @classmethod
    def set_item_compact(cls, item_id: str, value: dict) -> None:
        cls._client.set(
            f"compat:item:{item_id}",
            json.dumps(value, ensure_ascii=False),
            ex=cls._ttl_seconds,
        )