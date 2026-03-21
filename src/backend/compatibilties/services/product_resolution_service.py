import asyncio
from dataclasses import dataclass
from typing import Any

from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_service import (
    JobMetrics,
    READ_RATE_LIMITER,
    call_ml,
)
from services.excel_service import (
    extract_item_id,
    get_row_value,
    normalize_engine,
    normalize_text,
    normalize_transmission,
    parse_year_value,
)
from services.job_store import JobStore
from services.ml_client import ml_client
from services.product_cache_service import ProductCacheService


@dataclass
class ProductResolutionRow:
    original_row_index: int
    item_id: str | None
    brand_name: str
    model_name: str
    version_name: str
    engine_name: str
    transmission_name: str
    year: int | None


def map_row_to_resolution_input(row: dict, original_row_index: int) -> ProductResolutionRow:
    return ProductResolutionRow(
        original_row_index=original_row_index,
        item_id=extract_item_id(get_row_value(row, "ASOCIACION ML")),
        brand_name=normalize_text(get_row_value(row, "MARCA")),
        model_name=normalize_text(get_row_value(row, "MODELO")),
        version_name=normalize_text(get_row_value(row, "VERSION")),
        engine_name=normalize_engine(get_row_value(row, "CILINDRADA")),
        transmission_name=normalize_transmission(get_row_value(row, "TRANSMISION")),
        year=parse_year_value(get_row_value(row, "AÑO")),
    )


def build_resolution_key(site_id: str, row: ProductResolutionRow) -> str:
    return ProductCacheService.build_vehicle_key(
        site_id=site_id,
        brand_name=row.brand_name,
        model_name=row.model_name,
        year=row.year,
        version_name=row.version_name,
        engine_name=row.engine_name,
        transmission_name=row.transmission_name,
    )


def deduplicate_resolution_rows(rows: list[dict]) -> tuple[list[ProductResolutionRow], dict[str, list[int]]]:
    unique_rows: list[ProductResolutionRow] = []
    key_to_original_indices: dict[str, list[int]] = {}

    for idx, row in enumerate(rows):
        mapped = map_row_to_resolution_input(row, idx)
        key = "|".join(
            [
                mapped.brand_name.lower(),
                mapped.model_name.lower(),
                str(mapped.year or ""),
                mapped.version_name.lower(),
                mapped.engine_name.lower(),
                mapped.transmission_name.lower(),
            ]
        )

        if key not in key_to_original_indices:
            unique_rows.append(mapped)
            key_to_original_indices[key] = []

        key_to_original_indices[key].append(idx)

    return unique_rows, key_to_original_indices


def resolve_attribute_ids(
    catalog_cache: CatalogPreloadService,
    row: ProductResolutionRow,
) -> dict:
    brand_id = catalog_cache.resolve_brand_id(row.brand_name)
    model_id = catalog_cache.resolve_model_id(row.model_name)
    year_id = catalog_cache.resolve_year_id(row.year) if row.year is not None else None
    version_id = catalog_cache.resolve_version_id(row.version_name) if row.version_name else None
    engine_id = catalog_cache.resolve_engine_id(row.engine_name) if row.engine_name else None
    transmission_id = (
        catalog_cache.resolve_transmission_id(row.transmission_name)
        if row.transmission_name else None
    )

    return {
        "brand_id": brand_id,
        "model_id": model_id,
        "year_id": year_id,
        "version_id": version_id,
        "engine_id": engine_id,
        "transmission_id": transmission_id,
    }


async def resolve_single_product_id(
    *,
    access_token: str,
    site_id: str,
    row: ProductResolutionRow,
    catalog_cache: CatalogPreloadService,
    metrics: JobMetrics,
) -> dict:
    key = build_resolution_key(site_id, row)
    cached = ProductCacheService.get_product_resolution(key)
    if cached:
        return {
            **cached,
            "source": "cache",
        }

    ids = resolve_attribute_ids(catalog_cache, row)

    if not ids["brand_id"] or not ids["model_id"] or not ids["year_id"]:
        return {
            "ok": False,
            "item_id": row.item_id,
            "brand_name": row.brand_name,
            "model_name": row.model_name,
            "version_name": row.version_name,
            "engine_name": row.engine_name,
            "transmission_name": row.transmission_name,
            "year": row.year,
            **ids,
            "product_id": None,
            "error_code": "ATTRIBUTE_ID_NOT_FOUND",
            "error_message": "No fue posible resolver todos los IDs mínimos",
        }

    results = await call_ml(
        ml_client.search_vehicle_products,
        access_token=access_token,
        brand_id=ids["brand_id"],
        model_id=ids["model_id"],
        year_id=ids["year_id"],
        version_id=ids["version_id"],
        transmission_id=ids["transmission_id"],
        engine_id=ids["engine_id"],
        metrics=metrics,
        limiter=READ_RATE_LIMITER,
    )

    product_id = str(results[0]["id"]) if results and results[0].get("id") else None

    payload = {
        "ok": bool(product_id),
        "site_id": site_id,
        "item_id": row.item_id,
        "brand_name": row.brand_name,
        "model_name": row.model_name,
        "version_name": row.version_name,
        "engine_name": row.engine_name,
        "transmission_name": row.transmission_name,
        "year": row.year,
        **ids,
        "product_id": product_id,
        "error_code": None if product_id else "PRODUCT_NOT_FOUND",
        "error_message": None if product_id else "No se encontró product_id",
    }

    if product_id:
        ProductCacheService.set_product_resolution(key, payload)

    return {
        **payload,
        "source": "ml",
    }


async def resolve_products_from_rows(
    *,
    job_id: str,
    access_token: str,
    site_id: str,
    rows: list[dict],
    catalog_cache: CatalogPreloadService,
) -> dict:
    metrics = JobMetrics()
    unique_rows, key_to_original_indices = deduplicate_resolution_rows(rows)

    max_concurrency = max(1, int(getattr(settings, "product_resolution_concurrency", 5)))
    semaphore = asyncio.Semaphore(max_concurrency)
    progress_lock = asyncio.Lock()

    resolved_unique: list[dict | None] = [None] * len(unique_rows)
    completed = 0

    async def worker(pos: int, row: ProductResolutionRow):
        nonlocal completed
        async with semaphore:
            result = await resolve_single_product_id(
                access_token=access_token,
                site_id=site_id,
                row=row,
                catalog_cache=catalog_cache,
                metrics=metrics,
            )
            resolved_unique[pos] = result

            async with progress_lock:
                completed += 1
                if completed % 50 == 0 or completed == len(unique_rows):
                    progress = 10 + int((completed / max(1, len(unique_rows))) * 85)
                    JobStore.update(
                        job_id,
                        progress=min(progress, 95),
                        processed_unique_rows=completed,
                        message=f"Resolviendo product_id {completed}/{len(unique_rows)}",
                    )

    await asyncio.gather(*(worker(i, row) for i, row in enumerate(unique_rows)))

    unique_key_to_result: dict[str, dict] = {}
    for row, result in zip(unique_rows, resolved_unique):
        key = "|".join(
            [
                row.brand_name.lower(),
                row.model_name.lower(),
                str(row.year or ""),
                row.version_name.lower(),
                row.engine_name.lower(),
                row.transmission_name.lower(),
            ]
        )
        unique_key_to_result[key] = result or {
            "ok": False,
            "product_id": None,
            "error_code": "MISSING_RESULT",
            "error_message": "Resultado faltante",
        }

    final_rows: list[dict] = []
    for idx, row in enumerate(rows):
        mapped = map_row_to_resolution_input(row, idx)
        key = "|".join(
            [
                mapped.brand_name.lower(),
                mapped.model_name.lower(),
                str(mapped.year or ""),
                mapped.version_name.lower(),
                mapped.engine_name.lower(),
                mapped.transmission_name.lower(),
            ]
        )
        resolved = unique_key_to_result[key]
        final_rows.append(
            {
                "original_row_index": idx,
                "item_id": mapped.item_id,
                "brand_name": mapped.brand_name,
                "model_name": mapped.model_name,
                "version_name": mapped.version_name,
                "engine_name": mapped.engine_name,
                "transmission_name": mapped.transmission_name,
                "year": mapped.year,
                **resolved,
            }
        )

    ok_count = sum(1 for r in final_rows if r.get("ok") and r.get("product_id"))
    error_count = len(final_rows) - ok_count

    return {
        "rows": final_rows,
        "summary": {
            "total_rows": len(rows),
            "unique_combinations": len(unique_rows),
            "resolved_count": ok_count,
            "error_count": error_count,
            "metrics": metrics.to_dict(),
        },
    }