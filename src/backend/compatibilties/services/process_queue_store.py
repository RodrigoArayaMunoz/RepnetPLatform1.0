import json
import time
from typing import Any

import redis

from config import settings


class ProcessQueueStore:
    _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _state_key = "process_queue:state"
    _lock_key = "process_queue:lock"
    _state_ttl_seconds = 7 * 24 * 60 * 60
    _lock_ttl_seconds = 48 * 60 * 60

    @classmethod
    def _default_state(cls) -> dict[str, Any]:
        return {
            "running": False,
            "button_text": "Ejecutar procesos",
            "task_id": None,
            "user_id": None,
            "started_at": None,
            "completed_at": None,
            "current_process_row_id": None,
            "current_process_id": None,
            "current_filename": None,
            "current_process_type": None,
            "current_job_id": None,
            "processed_count": 0,
            "pending_count": 0,
            "next_run_at": None,
            "message": "Sin procesos en ejecucion",
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
    def try_start(cls, *, user_id: str, task_id: str | None = None) -> bool:
        acquired = cls._client.set(
            cls._lock_key,
            task_id or "pending",
            nx=True,
            ex=cls._lock_ttl_seconds,
        )
        if not acquired:
            return False

        state = cls._default_state()
        state.update(
            {
                "running": True,
                "button_text": "Procesos en ejecución",
                "task_id": task_id,
                "user_id": str(user_id),
                "started_at": time.time(),
                "message": "Cola de procesos iniciada",
            }
        )
        cls._client.set(
            cls._state_key,
            json.dumps(state, ensure_ascii=False),
            ex=cls._state_ttl_seconds,
        )
        return True

    @classmethod
    def update(cls, **kwargs) -> None:
        state = cls.get_state()
        state.update(kwargs)
        cls._client.set(
            cls._state_key,
            json.dumps(state, ensure_ascii=False),
            ex=cls._state_ttl_seconds,
        )

    @classmethod
    def reset(
        cls,
        *,
        message: str | None = None,
        last_error: str | None = None,
    ) -> None:
        state = cls._default_state()
        state.update(
            {
                "completed_at": time.time(),
                "message": message or state["message"],
                "last_error": last_error,
            }
        )
        cls._client.set(
            cls._state_key,
            json.dumps(state, ensure_ascii=False),
            ex=cls._state_ttl_seconds,
        )
        cls._client.delete(cls._lock_key)

    @classmethod
    def finish(cls, *, message: str, last_error: str | None = None) -> None:
        state = cls.get_state()
        state.update(
            {
                "running": False,
                "button_text": "Ejecutar procesos",
                "task_id": None,
                "completed_at": time.time(),
                "current_process_row_id": None,
                "current_process_id": None,
                "current_filename": None,
                "current_process_type": None,
                "current_job_id": None,
                "pending_count": 0,
                "next_run_at": None,
                "message": message,
                "last_error": last_error,
            }
        )
        cls._client.set(
            cls._state_key,
            json.dumps(state, ensure_ascii=False),
            ex=cls._state_ttl_seconds,
        )
        cls._client.delete(cls._lock_key)


process_queue_store = ProcessQueueStore()
