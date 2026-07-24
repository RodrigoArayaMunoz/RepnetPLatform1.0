import json
import time
from typing import Any

import redis

from config import settings


class PublicationSyncStore:
    _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _state_key = "publication_sync:state"
    _lock_key = "publication_sync:lock"
    _state_ttl_seconds = 7 * 24 * 60 * 60
    _lock_ttl_seconds = 12 * 60 * 60

    @classmethod
    def _default_state(cls) -> dict[str, Any]:
        return {
            "running": False,
            "status": "idle",
            "task_id": None,
            "user_id": None,
            "seller_id": None,
            "started_at": None,
            "finished_at": None,
            "progress": 0,
            "expected_count": 0,
            "scanned_count": 0,
            "processed_count": 0,
            "saved_count": 0,
            "failed_count": 0,
            "scan_pages": 0,
            "multiget_batches": 0,
            "message": "Sin carga de publicaciones en ejecución",
            "last_error": None,
        }

    @classmethod
    def get_state(cls) -> dict[str, Any]:
        raw = cls._client.get(cls._state_key)
        if not raw:
            return cls._default_state()

        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return cls._default_state()

        return {
            **cls._default_state(),
            **state,
        }

    @classmethod
    def try_start(cls, *, user_id: str) -> bool:
        acquired = cls._client.set(
            cls._lock_key,
            "pending",
            nx=True,
            ex=cls._lock_ttl_seconds,
        )
        if not acquired:
            return False

        state = cls._default_state()
        state.update(
            {
                "running": True,
                "status": "queued",
                "user_id": str(user_id),
                "started_at": time.time(),
                "message": "Carga de publicaciones encolada",
            }
        )
        cls._save(state)
        return True

    @classmethod
    def _save(cls, state: dict[str, Any]) -> None:
        cls._client.set(
            cls._state_key,
            json.dumps(state, ensure_ascii=False),
            ex=cls._state_ttl_seconds,
        )

    @classmethod
    def update(cls, **kwargs: Any) -> None:
        state = cls.get_state()
        state.update(kwargs)
        cls._save(state)

        if state.get("running"):
            cls._client.expire(cls._lock_key, cls._lock_ttl_seconds)

    @classmethod
    def finish(
        cls,
        *,
        status: str,
        message: str,
        last_error: str | None = None,
        **summary: Any,
    ) -> None:
        state = cls.get_state()
        state.update(summary)
        state.update(
            {
                "running": False,
                "status": status,
                "progress": 100 if status in {"success", "partial"} else state["progress"],
                "finished_at": time.time(),
                "message": message,
                "last_error": last_error,
            }
        )
        cls._save(state)
        cls._client.delete(cls._lock_key)

    @classmethod
    def reset(cls, *, message: str) -> None:
        state = cls._default_state()
        state.update(
            {
                "finished_at": time.time(),
                "message": message,
            }
        )
        cls._save(state)
        cls._client.delete(cls._lock_key)


publication_sync_store = PublicationSyncStore()
