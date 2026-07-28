import asyncio
from dataclasses import dataclass

from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_service import (
    JobCaches,
    JobMetrics,
    resolve_vehicle_product_row,
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
    familia: str = ""
    posicion_dt: str = ""
    posicion_id: str = ""


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
        familia=normalize_text(get_row_value(row, "FAMILIA")),
        posicion_dt=normalize_text(get_row_value(row, "POSICION_DT")),
        posicion_id=normalize_text(get_row_value(row, "POSICION_ID")),
    )


def deduplicate_resolution_rows(rows: list[dict]) -> tuple[list[ProductResolutionRow], dict[str, list[int]]]:
    unique_rows: list[ProductResolutionRow] = []
    key_to_original_indices: dict[str, list[int]] = {}

    for idx, row in enumerate(rows):
        mapped = map_row_to_resolution_input(row, idx)
        key = "|".join(
            [
                str(mapped.item_id or "").lower(),
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


async def resolve_single_product_id(
    *,
    access_token: str,
    user_id: int | str,
    site_id: str,
    row: ProductResolutionRow,
    catalog_cache: CatalogPreloadService,
    caches: JobCaches,
    metrics: JobMetrics,
) -> dict:
    result = await resolve_vehicle_product_row(
        access_token=access_token,
        user_id=user_id,
        row={
            "ASOCIACION ML": row.item_id,
            "MARCA": row.brand_name,
            "MODELO": row.model_name,
            "VERSION": row.version_name,
            "CILINDRADA": row.engine_name,
            "TRANSMISION": row.transmission_name,
            "AÑO": row.year,
            "FAMILIA": row.familia,
            "POSICION_DT": row.posicion_dt,
            "POSICION_ID": row.posicion_id,
        },
        catalog_cache=catalog_cache,
        caches=caches,
        metrics=metrics,
    )
    return {
        **result,
        "site_id": site_id,
        "source": "ml",
    }


async def resolve_products_from_rows(
    *,
    job_id: str,
    access_token: str,
    user_id: int | str,
    site_id: str,
    rows: list[dict],
    catalog_cache: CatalogPreloadService,
) -> dict:
    metrics = JobMetrics()
    caches = JobCaches()
    catalog_cache.metrics = metrics
    unique_rows, _key_to_original_indices = deduplicate_resolution_rows(rows)

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
                user_id=user_id,
                site_id=site_id,
                row=row,
                catalog_cache=catalog_cache,
                caches=caches,
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
                        message=(
                            "Validando familias de vehículos "
                            f"{completed}/{len(unique_rows)}"
                        ),
                    )

    await asyncio.gather(*(worker(i, row) for i, row in enumerate(unique_rows)))

    unique_key_to_result: dict[str, dict] = {}
    for row, result in zip(unique_rows, resolved_unique):
        key = "|".join(
            [
                str(row.item_id or "").lower(),
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
                str(mapped.item_id or "").lower(),
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
                **resolved,
                "original_row_index": idx,
                "item_id": mapped.item_id,
                "brand_name": mapped.brand_name,
                "model_name": mapped.model_name,
                "version_name": mapped.version_name,
                "engine_name": mapped.engine_name,
                "transmission_name": mapped.transmission_name,
                "year": mapped.year,
                "familia": mapped.familia,
                "posicion_dt": mapped.posicion_dt,
                "posicion_id": mapped.posicion_id,
            }
        )

    ok_count = sum(
        1
        for r in final_rows
        if r.get("ok") and isinstance(r.get("product_family"), dict)
    )
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
