"""Expiring catalog resolutions shared by workers using the uploads volume."""
import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def family_cache_key(attributes: list[dict]) -> str:
    payload = {"version": 1, "domain_id": settings.ml_domain_id,
               "attributes": sorted(attributes, key=lambda attribute: attribute["id"])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _access(key: str, value: dict | None = None) -> dict | None:
    ttl = settings.compatibility_family_cache_ttl_seconds
    if not ttl:
        return None
    path = Path(settings.upload_dir) / "compatibility-family-cache.sqlite3"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS resolutions (key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL)")
            if value is not None:
                connection.execute("INSERT OR REPLACE INTO resolutions VALUES (?, ?, ?)",
                                   (key, json.dumps(value), time.time() + ttl))
                connection.execute("DELETE FROM resolutions WHERE expires < ?", (time.time(),))
                return None
            row = connection.execute("SELECT payload FROM resolutions WHERE key = ? AND expires > ?", (key, time.time())).fetchone()
            payload = json.loads(row[0]) if row else None
            return payload if isinstance(payload, dict) else None
    except (OSError, sqlite3.Error, ValueError):
        logger.warning("[COMPATIBILITY][CACHE_UNAVAILABLE] using job-local cache")
        return None


async def get_family_resolution(key: str) -> dict | None:
    return await asyncio.to_thread(_access, key)


async def save_family_resolution(key: str, value: dict) -> None:
    await asyncio.to_thread(_access, key, value)
