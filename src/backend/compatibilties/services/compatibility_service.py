import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeAlias

from fastapi import HTTPException

from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.excel_service import (
    extract_item_id,
    get_row_value,
    normalize_engine,
    normalize_for_compare,
    normalize_text,
    normalize_transmission,
    parse_year_value,
)
from services.job_store import JobStore
from services.ml_client import ml_client
from services.redis_rate_limiter import RedisWindowRateLimiter


@dataclass
class JobCaches:
    item_detail: dict[str, dict] = field(default_factory=dict)
    product: dict[
        tuple[str | None, str | None, str | None, str | None, str | None, str | None],
        str | None,
    ] = field(default_factory=dict)


@dataclass
class JobMetrics:
    started_at: float = field(default_factory=time.monotonic)
    ml_requests: int = 0
    ml_retries: int = 0
    ml_rate_limited: int = 0
    ml_http_errors: int = 0
    ml_technical_errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def to_dict(self) -> dict:
        return {
            "duration_seconds": round(time.monotonic() - self.started_at, 2),
            "ml_requests": self.ml_requests,
            "ml_retries": self.ml_retries,
            "ml_rate_limited": self.ml_rate_limited,
            "ml_http_errors": self.ml_http_errors,
            "ml_technical_errors": self.ml_technical_errors,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


class SimpleRateLimiter:
    def __init__(self, requests_per_second: float):
        self.requests_per_second = max(requests_per_second, 0.1)
        self.min_interval = 1.0 / self.requests_per_second
        self._lock = asyncio.Lock()
        self._last_ts = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_time = self.min_interval - (now - self._last_ts)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_ts = time.monotonic()


def _settings_value(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


DEBUG_COMPAT = False


def dlog(*parts):
    if DEBUG_COMPAT:
        print(*parts, flush=True)


def dsep(title: str = ""):
    if DEBUG_COMPAT:
        line = "=" * 25
        print(f"\n{line} {title} {line}", flush=True)


READ_RATE_LIMITER = RedisWindowRateLimiter(
    redis_url=settings.redis_url,
    namespace="ml:read",
    requests_per_second=int(_settings_value("ml_read_requests_per_second", 2)),
)

WRITE_RATE_LIMITER = RedisWindowRateLimiter(
    redis_url=settings.redis_url,
    namespace="ml:write_compat",
    requests_per_second=int(_settings_value("ml_write_requests_per_second", 1)),
)

RETRY_ATTEMPTS = int(_settings_value("ml_retry_attempts", 4))
RETRY_BASE_DELAY = float(_settings_value("ml_retry_base_delay", 1.0))
PROGRESS_UPDATE_EVERY = int(_settings_value("job_progress_update_every", 25))

ProgressCallback: TypeAlias = Callable[[int], Awaitable[None]]


def _is_retryable_http_exception(exc: HTTPException) -> bool:
    status = getattr(exc, "status_code", None)
    return status in {429, 500, 502, 503, 504}


async def call_ml(
    fn: Callable[..., Awaitable[Any]],
    *args,
    metrics: JobMetrics,
    limiter=None,
    **kwargs,
) -> Any:
    last_exc: Exception | None = None
    limiter = limiter or READ_RATE_LIMITER

    for attempt in range(RETRY_ATTEMPTS):
        try:
            await limiter.acquire()
            metrics.ml_requests += 1
            return await fn(*args, **kwargs)

        except HTTPException as exc:
            last_exc = exc

            if getattr(exc, "status_code", None) == 429:
                metrics.ml_rate_limited += 1

            if not _is_retryable_http_exception(exc) or attempt == RETRY_ATTEMPTS - 1:
                metrics.ml_http_errors += 1
                raise

            metrics.ml_retries += 1

            if getattr(exc, "status_code", None) == 429:
                delay = max(2.0, RETRY_BASE_DELAY * (2 ** attempt)) + random.uniform(0, 0.5)
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.4)

            await asyncio.sleep(delay)

        except Exception as exc:
            last_exc = exc

            if attempt == RETRY_ATTEMPTS - 1:
                metrics.ml_technical_errors += 1
                raise

            metrics.ml_retries += 1
            delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.4)
            await asyncio.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError("call_ml terminó sin respuesta ni excepción")


def _cache_get(cache: dict, key: Any, metrics: JobMetrics) -> Any:
    if key in cache:
        metrics.cache_hits += 1
        return cache[key]
    metrics.cache_misses += 1
    return None


async def search_vehicle_product_id(
    access_token: str,
    brand_id: str | None,
    model_id: str | None,
    year_id: str | None,
    version_id: str | None,
    transmission_id: str | None,
    engine_id: str | None,
    caches: JobCaches,
    metrics: JobMetrics,
) -> str | None:
    key = (brand_id, model_id, year_id, version_id, transmission_id, engine_id)
    cached = _cache_get(caches.product, key, metrics)
    if key in caches.product:
        return cached

    results = await call_ml(
        ml_client.search_vehicle_products,
        access_token=access_token,
        brand_id=brand_id,
        model_id=model_id,
        year_id=year_id,
        version_id=version_id,
        transmission_id=transmission_id,
        engine_id=engine_id,
        metrics=metrics,
        limiter=READ_RATE_LIMITER,
    )

    value = str(results[0]["id"]) if results and results[0].get("id") else None
    caches.product[key] = value
    return value


async def get_item_detail_cached(
    access_token: str,
    item_id: str,
    caches: JobCaches,
    metrics: JobMetrics,
) -> dict:
    cached = _cache_get(caches.item_detail, item_id, metrics)
    if item_id in caches.item_detail:
        return cached

    data = await call_ml(
        ml_client.get_item_detail,
        access_token,
        item_id,
        metrics=metrics,
        limiter=READ_RATE_LIMITER,
    )
    caches.item_detail[item_id] = data
    return data


def dedup_key(row: dict) -> tuple:
    item_id = extract_item_id(get_row_value(row, "ASOCIACION ML"))
    brand_name = normalize_for_compare(get_row_value(row, "MARCA"))
    model_name = normalize_for_compare(get_row_value(row, "MODELO"))
    version_name = normalize_for_compare(get_row_value(row, "VERSION"))
    engine_name = normalize_for_compare(get_row_value(row, "CILINDRADA"))
    transmission_name = normalize_for_compare(get_row_value(row, "TRANSMISION"))
    year = parse_year_value(get_row_value(row, "AÑO"))

    return (
        item_id,
        brand_name,
        model_name,
        version_name,
        engine_name,
        transmission_name,
        year,
    )


def vehicle_resolution_key(row: dict) -> tuple:
    brand_name = normalize_for_compare(get_row_value(row, "MARCA"))
    model_name = normalize_for_compare(get_row_value(row, "MODELO"))
    version_name = normalize_for_compare(get_row_value(row, "VERSION"))
    engine_name = normalize_for_compare(get_row_value(row, "CILINDRADA"))
    transmission_name = normalize_for_compare(get_row_value(row, "TRANSMISION"))
    year = parse_year_value(get_row_value(row, "AÑO"))

    return (
        brand_name,
        model_name,
        version_name,
        engine_name,
        transmission_name,
        year,
    )


def build_vehicle_resolution_plan(rows: list[dict]) -> tuple[list[dict], list[list[int]]]:
    unique_key_to_index: dict[tuple, int] = {}
    unique_entries: list[dict] = []
    original_indices_by_unique_index: list[list[int]] = []

    for original_idx, row in enumerate(rows):
        key = vehicle_resolution_key(row)
        unique_index = unique_key_to_index.get(key)

        if unique_index is None:
            unique_index = len(unique_entries)
            unique_key_to_index[key] = unique_index
            unique_entries.append(
                {
                    "unique_index": unique_index,
                    "row": row,
                }
            )
            original_indices_by_unique_index.append([])

        original_indices_by_unique_index[unique_index].append(original_idx)

    return unique_entries, original_indices_by_unique_index


def build_unique_rows_plan(rows: list[dict]) -> tuple[list[dict], list[list[int]]]:
    unique_key_to_index: dict[tuple, int] = {}
    unique_entries: list[dict] = []
    original_indices_by_unique_index: list[list[int]] = []

    for original_idx, row in enumerate(rows):
        key = dedup_key(row)
        unique_index = unique_key_to_index.get(key)

        if unique_index is None:
            unique_index = len(unique_entries)
            unique_key_to_index[key] = unique_index
            unique_entries.append(
                {
                    "unique_index": unique_index,
                    "row": row,
                }
            )
            original_indices_by_unique_index.append([])

        original_indices_by_unique_index[unique_index].append(original_idx)

    return unique_entries, original_indices_by_unique_index


def build_results_summary(
    final_results: list[dict],
    *,
    total_rows: int,
    total_unique_rows: int,
    duplicated_rows: int,
    metrics: JobMetrics | dict | None = None,
) -> dict:
    rows_ok = sum(1 for r in final_results if r.get("ok"))
    rows_error = len(final_results) - rows_ok

    compat_total = 0
    compat_ok = 0
    compat_error = 0
    functional_errors = 0
    technical_errors = 0

    for r in final_results:
        if r.get("error_type") == "functional":
            functional_errors += 1
        elif r.get("error_type") == "technical":
            technical_errors += 1

        details = r.get("results", [])
        if not isinstance(details, list):
            details = []

        if details:
            compat_total += len(details)
            compat_ok += sum(1 for d in details if d.get("ok"))
            compat_error += sum(1 for d in details if not d.get("ok"))
        else:
            compat_total += 1
            if r.get("ok") is True:
                compat_ok += 1
            else:
                compat_error += 1

    if metrics is None:
        metrics_payload = {}
    elif isinstance(metrics, dict):
        metrics_payload = metrics
    else:
        metrics_payload = metrics.to_dict()

    return {
        "processed_rows": total_rows,
        "unique_rows": total_unique_rows,
        "duplicated_rows": duplicated_rows,
        "success_count": rows_ok,
        "error_count": rows_error,
        "functional_errors": functional_errors,
        "technical_errors": technical_errors,
        "compatibilities_total": compat_total,
        "compatibilities_ok": compat_ok,
        "compatibilities_error": compat_error,
        "metrics": metrics_payload,
    }


def _build_error_result(
    item_id: str | None,
    reason: str,
    *,
    brand_name: str = "",
    model_name: str = "",
    version_name: str = "",
    engine_name: str = "",
    transmission_name: str = "",
    year: int | None = None,
    error_type: str = "functional",
    error_code: str = "VALIDATION_ERROR",
    results: list[dict] | None = None,
) -> dict:
    error_details = results
    if error_details is None:
        error_details = [
            {
                "ok": False,
                "year": year,
                "reason": reason,
                "error_type": error_type,
                "error_code": error_code,
                "brand_name": brand_name,
                "model_name": model_name,
                "version_name": version_name,
                "engine_name": engine_name,
                "transmission_name": transmission_name,
                "item_id": item_id,
            }
        ]

    return {
        "ok": False,
        "item_id": item_id,
        "brand_name": brand_name,
        "model_name": model_name,
        "version_name": version_name,
        "engine_name": engine_name,
        "transmission_name": transmission_name,
        "year_requested": year,
        "success_count": 0,
        "error_count": 1,
        "error_type": error_type,
        "error_code": error_code,
        "reason": reason,
        "results": error_details,
    }


async def resolve_vehicle_product_row(
    access_token: str,
    row: dict,
    catalog_cache: CatalogPreloadService,
    caches: JobCaches,
    metrics: JobMetrics,
) -> dict:
    item_id = extract_item_id(get_row_value(row, "ASOCIACION ML"))
    brand_name = normalize_text(get_row_value(row, "MARCA"))
    model_name = normalize_text(get_row_value(row, "MODELO"))
    version_name = normalize_text(get_row_value(row, "VERSION"))
    engine_name = normalize_engine(get_row_value(row, "CILINDRADA"))
    transmission_name = normalize_transmission(get_row_value(row, "TRANSMISION"))
    year = parse_year_value(get_row_value(row, "AÑO"))
    familia = normalize_text(get_row_value(row, "FAMILIA"))
    posicion_dt = normalize_text(get_row_value(row, "POSICION_DT"))
    posicion_id = normalize_text(get_row_value(row, "POSICION_ID"))

    if not item_id:
        return _build_error_result(
            None,
            "Fila sin ASOCIACION ML",
            brand_name=brand_name,
            model_name=model_name,
            version_name=version_name,
            engine_name=engine_name,
            transmission_name=transmission_name,
            year=year,
            error_code="MISSING_ITEM_ID",
        )

    if not brand_name or not model_name or year is None:
        return _build_error_result(
            item_id,
            "Faltan datos mínimos: MARCA / MODELO / AÑO",
            brand_name=brand_name,
            model_name=model_name,
            version_name=version_name,
            engine_name=engine_name,
            transmission_name=transmission_name,
            year=year,
            error_code="MISSING_MINIMUM_DATA",
        )

    try:
        brand_id = catalog_cache.resolve_brand_id(brand_name)
        if not brand_id:
            return _build_error_result(
                item_id,
                f"No se encontró BRAND para '{brand_name}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="BRAND_NOT_FOUND",
            )

        model_id = catalog_cache.resolve_model_id(model_name)
        if not model_id:
            return _build_error_result(
                item_id,
                f"No se encontró MODEL para '{model_name}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="MODEL_NOT_FOUND",
            )

        year_id = catalog_cache.resolve_year_id(year)
        if not year_id:
            return _build_error_result(
                item_id,
                f"No se encontró YEAR para '{year}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="YEAR_NOT_FOUND",
            )

        version_id = catalog_cache.resolve_version_id(version_name) if version_name else None
        if version_name and not version_id:
            return _build_error_result(
                item_id,
                f"No se encontró VERSION para '{version_name}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="VERSION_NOT_FOUND",
            )

        engine_id = catalog_cache.resolve_engine_id(engine_name) if engine_name else None
        if engine_name and not engine_id:
            return _build_error_result(
                item_id,
                f"No se encontró ENGINE para '{engine_name}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="ENGINE_NOT_FOUND",
            )

        transmission_id = (
            catalog_cache.resolve_transmission_id(transmission_name)
            if transmission_name
            else None
        )
        if transmission_name and not transmission_id:
            return _build_error_result(
                item_id,
                f"No se encontró TRANSMISSION para '{transmission_name}'",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_code="TRANSMISSION_NOT_FOUND",
            )

        product_id = await search_vehicle_product_id(
            access_token=access_token,
            brand_id=brand_id,
            model_id=model_id,
            year_id=year_id,
            version_id=version_id,
            transmission_id=transmission_id,
            engine_id=engine_id,
            caches=caches,
            metrics=metrics,
        )

        if not product_id:
            return _build_error_result(
                item_id,
                "No se encontró product_id con los ids resueltos",
                brand_name=brand_name,
                model_name=model_name,
                version_name=version_name,
                engine_name=engine_name,
                transmission_name=transmission_name,
                year=year,
                error_type="functional",
                error_code="PRODUCT_NOT_FOUND",
                results=[
                    {
                        "ok": False,
                        "year": year,
                        "reason": "No se encontró product_id con los ids resueltos",
                        "error_type": "functional",
                        "error_code": "PRODUCT_NOT_FOUND",
                    }
                ],
            )

        return {
            "ok": True,
            "item_id": item_id,
            "brand_name": brand_name,
            "model_name": model_name,
            "version_name": version_name,
            "engine_name": engine_name,
            "transmission_name": transmission_name,
            "year_requested": year,
            "year_processed": year,
            "year": year,
            "product_id": product_id,
            "familia": familia,
            "posicion_dt": posicion_dt,
            "posicion_id": posicion_id,
            "success_count": 1,
            "error_count": 0,
            "results": [
                {
                    "ok": True,
                    "year": year,
                    "product_id": product_id,
                }
            ],
        }

    except HTTPException as exc:
        return _build_error_result(
            item_id,
            f"HTTPException: {exc.detail}",
            brand_name=brand_name,
            model_name=model_name,
            version_name=version_name,
            engine_name=engine_name,
            transmission_name=transmission_name,
            year=year,
            error_type="technical" if _is_retryable_http_exception(exc) else "functional",
            error_code=f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
        )
    except Exception as exc:
        return _build_error_result(
            item_id,
            f"Exception: {str(exc)}",
            brand_name=brand_name,
            model_name=model_name,
            version_name=version_name,
            engine_name=engine_name,
            transmission_name=transmission_name,
            year=year,
            error_type="technical",
            error_code="ROW_PROCESSING_EXCEPTION",
        )


def expand_resolved_rows_to_originals(
    *,
    unique_results: list[dict],
    original_rows: list[dict],
    original_indices_by_unique_index: list[list[int]],
) -> list[dict]:
    expanded: list[dict] = [None] * len(original_rows)  # type: ignore

    for unique_index, resolved in enumerate(unique_results):
        original_indices = original_indices_by_unique_index[unique_index]

        for original_idx in original_indices:
            original_row = original_rows[original_idx]

            original_item_id = extract_item_id(get_row_value(original_row, "ASOCIACION ML"))
            brand_name = normalize_text(get_row_value(original_row, "MARCA"))
            model_name = normalize_text(get_row_value(original_row, "MODELO"))
            version_name = normalize_text(get_row_value(original_row, "VERSION"))
            engine_name = normalize_engine(get_row_value(original_row, "CILINDRADA"))
            transmission_name = normalize_transmission(get_row_value(original_row, "TRANSMISION"))
            year = parse_year_value(get_row_value(original_row, "AÑO"))

            row_copy = dict(resolved)

            # MUY IMPORTANTE: sobreescribir con los valores originales de la fila
            row_copy["item_id"] = original_item_id
            row_copy["brand_name"] = brand_name
            row_copy["model_name"] = model_name
            row_copy["version_name"] = version_name
            row_copy["engine_name"] = engine_name
            row_copy["transmission_name"] = transmission_name
            row_copy["year"] = year
            row_copy["year_requested"] = year
            row_copy["year_processed"] = year
            row_copy["original_row_index"] = original_idx
            row_copy["was_duplicated_vehicle"] = len(original_indices) > 1

            # Propagar columnas de restricción desde la fila original
            row_copy["familia"] = normalize_text(get_row_value(original_row, "FAMILIA"))
            row_copy["posicion_dt"] = normalize_text(get_row_value(original_row, "POSICION_DT"))
            row_copy["posicion_id"] = normalize_text(get_row_value(original_row, "POSICION_ID"))

            expanded[original_idx] = row_copy

    return [
        row if row is not None else {
            "ok": False,
            "reason": "Resultado expandido faltante",
            "error_type": "technical",
            "error_code": "MISSING_EXPANDED_RESULT",
            "results": [],
        }
        for row in expanded
    ]

async def process_unique_rows_chunk(
    access_token: str,
    unique_entries: list[dict],
    catalog_cache: CatalogPreloadService,
    on_progress: Callable[[int], Awaitable[None]] | None = None,
) -> dict:
    caches = JobCaches()
    metrics = JobMetrics()

    max_concurrency = max(1, int(_settings_value("max_row_concurrency", 2)))
    progress_every = max(1, int(_settings_value("chunk_progress_update_every", 25)))

    semaphore = asyncio.Semaphore(max_concurrency)
    progress_lock = asyncio.Lock()

    total_entries = len(unique_entries)
    row_results: list[dict | None] = [None] * total_entries
    completed = 0

    if total_entries == 0:
        summary = build_results_summary(
            [],
            total_rows=0,
            total_unique_rows=0,
            duplicated_rows=0,
            metrics=metrics,
        )
        summary["processed_unique_rows"] = 0
        return {
            "results": [],
            "summary": summary,
        }

    async def worker(pos: int, entry: dict):
        nonlocal completed

        async with semaphore:
            result = await resolve_vehicle_product_row(
                access_token=access_token,
                row=entry["row"],
                catalog_cache=catalog_cache,
                caches=caches,
                metrics=metrics,
            )

            result["unique_index"] = entry["unique_index"]
            row_results[pos] = result

            should_report = False
            completed_snapshot = 0

            async with progress_lock:
                completed += 1
                completed_snapshot = completed
                should_report = (
                    on_progress is not None
                    and (
                        completed_snapshot % progress_every == 0
                        or completed_snapshot == total_entries
                    )
                )

            if should_report and on_progress is not None:
                try:
                    await on_progress(completed_snapshot)
                except Exception:
                    pass

    await asyncio.gather(*(worker(i, entry) for i, entry in enumerate(unique_entries)))

    final_results = [
        r if r is not None else {
            "ok": False,
            "reason": "Resultado faltante",
            "error_type": "technical",
            "error_code": "MISSING_RESULT",
            "results": [],
        }
        for r in row_results
    ]

    summary = build_results_summary(
        final_results,
        total_rows=total_entries,
        total_unique_rows=total_entries,
        duplicated_rows=0,
        metrics=metrics,
    )
    summary["processed_unique_rows"] = total_entries

    return {
        "results": final_results,
        "summary": summary,
    }


async def process_rows_for_job(
    job_id: str,
    access_token: str,
    rows: list[dict],
) -> dict:
    caches = JobCaches()
    metrics = JobMetrics()
    catalog_cache = CatalogPreloadService(call_ml=call_ml, metrics=metrics)

    JobStore.update(
        job_id,
        progress=3,
        message="Precargando diccionarios globales desde Mercado Libre...",
    )

    catalog_data = await catalog_cache.preload_all(access_token)

    JobStore.update(
        job_id,
        progress=10,
        message="Diccionarios precargados. Resolviendo product_id por vehículo único...",
        metrics={
            **metrics.to_dict(),
            "catalog_preload": catalog_data.stats() if hasattr(catalog_data, "stats") else {},
        },
    )

    total_rows = len(rows)

    unique_entries, original_indices_by_unique_index = build_vehicle_resolution_plan(rows)
    total_unique_rows = len(unique_entries)
    duplicated_rows = total_rows - total_unique_rows

    if total_unique_rows == 0:
        JobStore.update(
            job_id,
            progress=95,
            message="No hay filas válidas para procesar",
            metrics=metrics.to_dict(),
        )
        return {
            "results": [],
            "summary": {
                "processed_rows": 0,
                "unique_rows": 0,
                "duplicated_rows": 0,
                "success_count": 0,
                "error_count": 0,
                "functional_errors": 0,
                "technical_errors": 0,
                "compatibilities_total": 0,
                "compatibilities_ok": 0,
                "compatibilities_error": 0,
                "metrics": metrics.to_dict(),
            },
        }

    semaphore = asyncio.Semaphore(int(_settings_value("max_row_concurrency", 3)))
    progress_lock = asyncio.Lock()

    completed = 0
    unique_results: list[dict | None] = [None] * total_unique_rows

    async def worker(pos: int, entry: dict):
        nonlocal completed

        async with semaphore:
            result = await resolve_vehicle_product_row(
                access_token=access_token,
                row=entry["row"],
                catalog_cache=catalog_cache,
                caches=caches,
                metrics=metrics,
            )
            unique_results[pos] = result

            async with progress_lock:
                completed += 1
                if completed % PROGRESS_UPDATE_EVERY == 0 or completed == total_unique_rows:
                    progress = 10 + int((completed / total_unique_rows) * 85)
                    JobStore.update(
                        job_id,
                        progress=progress,
                        processed_rows=completed,
                        message=(
                            f"Resolviendo vehículos únicos {completed}/{total_unique_rows} "
                            f"para expandir a {total_rows} filas del Excel"
                        ),
                    )

    await asyncio.gather(*(worker(i, entry) for i, entry in enumerate(unique_entries)))

    final_unique_results = [
        r if r is not None else {
            "ok": False,
            "reason": "La fila no devolvió resultado",
            "error_type": "technical",
            "error_code": "MISSING_RESULT",
            "results": [],
        }
        for r in unique_results
    ]

    expanded_results = expand_resolved_rows_to_originals(
        unique_results=final_unique_results,
        original_rows=rows,
        original_indices_by_unique_index=original_indices_by_unique_index,
    )

    JobStore.update(
        job_id,
        progress=95,
        message="Consolidando resultados finales...",
        metrics=metrics.to_dict(),
    )

    summary = build_results_summary(
        expanded_results,
        total_rows=total_rows,
        total_unique_rows=total_unique_rows,
        duplicated_rows=duplicated_rows,
        metrics=metrics,
    )

    return {
        "results": expanded_results,
        "summary": summary,
    }