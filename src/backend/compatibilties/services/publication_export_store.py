import hashlib

import redis

from config import settings


class PublicationExportStore:
    _COMPARE_DELETE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    _COMPARE_EXPIRE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(self) -> None:
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    @staticmethod
    def _reference_key(
        requested_by_user_id: str,
        seller_id: str,
        creation_date: str,
    ) -> str:
        identity = (
            f"{requested_by_user_id}:{seller_id}:{creation_date}"
        ).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()
        return f"publication-export:reference:{digest}"

    @staticmethod
    def _run_lock_key(job_id: str) -> str:
        return f"publication-export:run-lock:{job_id}"

    @staticmethod
    def _recovery_guard_key(job_id: str) -> str:
        return f"publication-export:recovery-guard:{job_id}"

    def get_referenced_job_id(
        self,
        *,
        requested_by_user_id: str,
        seller_id: str,
        creation_date: str,
    ) -> str | None:
        return self._client.get(
            self._reference_key(
                requested_by_user_id,
                seller_id,
                creation_date,
            )
        )

    def claim_reference(
        self,
        *,
        requested_by_user_id: str,
        seller_id: str,
        creation_date: str,
        job_id: str,
    ) -> bool:
        return bool(
            self._client.set(
                self._reference_key(
                    requested_by_user_id,
                    seller_id,
                    creation_date,
                ),
                job_id,
                nx=True,
                ex=int(
                    settings.ml_publication_export_artifact_ttl_seconds
                ),
            )
        )

    def release_reference(
        self,
        *,
        requested_by_user_id: str,
        seller_id: str,
        creation_date: str,
        job_id: str,
    ) -> None:
        self._client.eval(
            self._COMPARE_DELETE_SCRIPT,
            1,
            self._reference_key(
                requested_by_user_id,
                seller_id,
                creation_date,
            ),
            job_id,
        )

    def try_acquire_run_lock(
        self,
        *,
        job_id: str,
        owner: str,
    ) -> bool:
        return bool(
            self._client.set(
                self._run_lock_key(job_id),
                owner,
                nx=True,
                ex=int(settings.ml_publication_export_lock_ttl_seconds),
            )
        )

    def renew_run_lock(self, *, job_id: str, owner: str) -> bool:
        result = self._client.eval(
            self._COMPARE_EXPIRE_SCRIPT,
            1,
            self._run_lock_key(job_id),
            owner,
            int(settings.ml_publication_export_lock_ttl_seconds),
        )
        return bool(result)

    def release_run_lock(self, *, job_id: str, owner: str) -> None:
        self._client.eval(
            self._COMPARE_DELETE_SCRIPT,
            1,
            self._run_lock_key(job_id),
            owner,
        )

    def has_run_lock(self, job_id: str) -> bool:
        return bool(self._client.exists(self._run_lock_key(job_id)))

    def run_lock_ttl(self, job_id: str) -> int:
        return max(0, int(self._client.ttl(self._run_lock_key(job_id))))

    def try_acquire_recovery_guard(self, job_id: str) -> bool:
        return bool(
            self._client.set(
                self._recovery_guard_key(job_id),
                "1",
                nx=True,
                ex=60,
            )
        )


publication_export_store = PublicationExportStore()
