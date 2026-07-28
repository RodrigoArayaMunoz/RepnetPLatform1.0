import json
import uuid
from typing import Optional

import redis
from config import settings


class JobStore:
    _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    _ttl_seconds = 7 * 24 * 60 * 60  # 7 días

    @classmethod
    def _key(cls, job_id: str) -> str:
        return f"job:{job_id}"

    @classmethod
    def create(cls, filename: str) -> dict:
        job_id = str(uuid.uuid4())
        data = {
            "id": job_id,
            "filename": filename,
            "status": "uploaded",
            "message": "Archivo cargado correctamente",
            "progress": 0,
            "xlsx_path": None,
            "total_rows": 0,
            "total_unique_rows": 0,
            "processed_rows": 0,
            "processed_unique_rows": 0,
            "compatibilities_created": 0,
            "total_chunks": 0,
            "completed_chunks": 0,
            "result_path": None,
            "summary": {},
            "task_id": None,
        }

        cls._client.set(
            cls._key(job_id),
            json.dumps(data, ensure_ascii=False),
            ex=cls._ttl_seconds,
        )
        return data

    @classmethod
    def get(cls, job_id: str) -> Optional[dict]:
        raw = cls._client.get(cls._key(job_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @classmethod
    def update(cls, job_id: str, **kwargs) -> None:
        key = cls._key(job_id)
        raw = cls._client.get(key)
        if not raw:
            return

        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            return

        job.update(kwargs)

        cls._client.set(
            key,
            json.dumps(job, ensure_ascii=False),
            ex=cls._ttl_seconds,
        )

    @classmethod
    def update_progress(cls, job_id: str, progress: int, message: str) -> None:
        progress = max(0, min(100, int(progress)))
        cls.update(job_id, progress=progress, message=message)

    @classmethod
    def initialize_chunk_plan(
        cls,
        job_id: str,
        *,
        total_rows: int,
        total_unique_rows: int,
        total_chunks: int,
        message: str,
    ) -> None:
        cls.update(
            job_id,
            total_rows=total_rows,
            total_unique_rows=total_unique_rows,
            processed_rows=0,
            processed_unique_rows=0,
            total_chunks=total_chunks,
            completed_chunks=0,
            progress=10,
            message=message,
        )

    @classmethod
    def increment_chunk_progress(
        cls,
        job_id: str,
        *,
        processed_unique_rows_delta: int,
        total_unique_rows: int,
    ) -> None:
        key = cls._key(job_id)

        with cls._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw:
                        pipe.unwatch()
                        return

                    job = json.loads(raw)

                    current_processed = int(job.get("processed_unique_rows", 0))
                    current_completed_chunks = int(job.get("completed_chunks", 0))

                    new_processed = min(
                        total_unique_rows,
                        current_processed + int(processed_unique_rows_delta),
                    )
                    new_completed_chunks = current_completed_chunks + 1

                    progress = 10 + int((new_processed / max(total_unique_rows, 1)) * 85)
                    progress = min(progress, 95)

                    job["processed_unique_rows"] = new_processed
                    job["completed_chunks"] = new_completed_chunks
                    job["progress"] = progress
                    job["message"] = (
                        f"Procesadas {new_processed}/{total_unique_rows} filas únicas "
                        f"en {new_completed_chunks}/{job.get('total_chunks', 0)} chunks"
                    )

                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(job, ensure_ascii=False),
                        ex=cls._ttl_seconds,
                    )
                    pipe.execute()
                    return

                except redis.WatchError:
                    continue

    @classmethod
    def delete(cls, job_id: str) -> None:
        cls._client.delete(cls._key(job_id))


    @classmethod
    def increment_processed_unique_rows(
        cls,
        job_id: str,
        *,
        delta: int,
        total_unique_rows: int,
    ) -> None:
        key = cls._key(job_id)

        with cls._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw:
                        pipe.unwatch()
                        return

                    job = json.loads(raw)

                    current_processed = int(job.get("processed_unique_rows", 0))
                    new_processed = min(
                        total_unique_rows,
                        current_processed + int(delta),
                    )

                    progress = 10 + int((new_processed / max(total_unique_rows, 1)) * 85)
                    progress = min(progress, 95)

                    job["processed_unique_rows"] = new_processed
                    job["progress"] = progress
                    job["message"] = (
                        f"Procesadas {new_processed}/{total_unique_rows} filas únicas"
                    )

                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(job, ensure_ascii=False),
                        ex=cls._ttl_seconds,
                    )
                    pipe.execute()
                    return

                except redis.WatchError:
                    continue

    @classmethod
    def mark_chunk_completed(cls, job_id: str) -> None:
        key = cls._key(job_id)

        with cls._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw:
                        pipe.unwatch()
                        return

                    job = json.loads(raw)
                    job["completed_chunks"] = int(job.get("completed_chunks", 0)) + 1

                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(job, ensure_ascii=False),
                        ex=cls._ttl_seconds,
                    )
                    pipe.execute()
                    return

                except redis.WatchError:
                    continue
