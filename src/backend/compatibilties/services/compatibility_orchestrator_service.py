import asyncio
import logging

from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_batch_service import (
    build_compat_summary,
    process_compatibility_batches,
)
from services.compatibility_service import (
    JobCaches,
    JobMetrics,
    build_vehicle_resolution_plan,
    call_ml,
    process_rows_for_job,
)
from services.job_store import JobStore
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_compatibility_chunk_pause_seconds,
    get_process_file_chunk_size,
)

logger = logging.getLogger(__name__)


def _empty_resolution_summary() -> dict:
    return {
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
    }


async def process_excel_compatibilities_end_to_end(
    *,
    job_id: str,
    access_token: str,
    user_id: int | str,
    rows: list[dict],
) -> dict:
    total_rows = len(rows)
    chunk_size = get_process_file_chunk_size()
    pause_seconds = get_compatibility_chunk_pause_seconds()
    total_chunks = count_chunks(total_rows, chunk_size)
    unique_entries, _ = build_vehicle_resolution_plan(rows)
    total_unique_rows = len(unique_entries)
    duplicated_rows = total_rows - total_unique_rows

    JobStore.update(
        job_id,
        status="processing",
        progress=1,
        total_rows=total_rows,
        total_unique_rows=total_unique_rows,
        total_chunks=total_chunks,
        completed_chunks=0,
        processed_rows=0,
        processed_unique_rows=0,
        message="Preparando procesamiento de compatibilidades...",
    )

    if total_rows == 0:
        resolution_summary = _empty_resolution_summary()
        final_summary = {
            **build_compat_summary([], [], JobMetrics()),
            "resolution_summary": resolution_summary,
        }
        JobStore.update(
            job_id,
            progress=100,
            message="No hay filas para procesar",
            summary=final_summary,
            results=[],
        )
        return {
            "results": [],
            "batch_results": [],
            "summary": final_summary,
        }

    metrics = JobMetrics()
    caches = JobCaches()
    catalog_cache = CatalogPreloadService(call_ml=call_ml, metrics=metrics)

    JobStore.update(
        job_id,
        progress=3,
        message="Preparando catálogo de Mercado Libre...",
    )
    catalog_data = await catalog_cache.preload_all(
        access_token,
        user_id=user_id,
    )
    JobStore.update(
        job_id,
        progress=8,
        message=f"Catálogo preparado. Se procesarán {total_chunks} bloques.",
        metrics={
            **metrics.to_dict(),
            "catalog_preload": (
                catalog_data.stats() if hasattr(catalog_data, "stats") else {}
            ),
        },
    )

    all_batch_results: list[dict] = []
    all_final_rows: list[dict] = []
    completed_rows = 0
    resolution_summary = _empty_resolution_summary()

    for chunk_number, (start_index, chunk_rows) in enumerate(
        chunk_sequence(rows, chunk_size),
        start=1,
    ):
        chunk_start = start_index + 1
        chunk_end = start_index + len(chunk_rows)

        JobStore.update(
            job_id,
            progress=max(8, 10 + int((completed_rows / max(total_rows, 1)) * 80)),
            message=(
                f"Procesando bloque {chunk_number}/{total_chunks}: "
                f"filas {chunk_start}-{chunk_end}"
            ),
        )

        chunk_resolution = await process_rows_for_job(
            job_id=job_id,
            access_token=access_token,
            user_id=user_id,
            rows=chunk_rows,
            catalog_cache=catalog_cache,
            caches=caches,
            metrics=metrics,
            manage_job_updates=False,
        )
        resolved_rows = chunk_resolution.get("results", [])
        chunk_resolution_summary = chunk_resolution.get("summary", {})

        resolution_summary["processed_rows"] += int(
            chunk_resolution_summary.get("processed_rows", 0)
        )
        resolution_summary["success_count"] += int(
            chunk_resolution_summary.get("success_count", 0)
        )
        resolution_summary["error_count"] += int(
            chunk_resolution_summary.get("error_count", 0)
        )
        resolution_summary["functional_errors"] += int(
            chunk_resolution_summary.get("functional_errors", 0)
        )
        resolution_summary["technical_errors"] += int(
            chunk_resolution_summary.get("technical_errors", 0)
        )
        resolution_summary["compatibilities_total"] += int(
            chunk_resolution_summary.get("compatibilities_total", 0)
        )
        resolution_summary["compatibilities_ok"] += int(
            chunk_resolution_summary.get("compatibilities_ok", 0)
        )
        resolution_summary["compatibilities_error"] += int(
            chunk_resolution_summary.get("compatibilities_error", 0)
        )

        async def on_batch_progress(completed_batches: int, total_batches: int) -> None:
            local_ratio = completed_batches / max(total_batches, 1)
            completed_equivalent_rows = completed_rows + int(local_ratio * len(chunk_rows))
            progress = 10 + int(
                (completed_equivalent_rows / max(total_rows, 1)) * 85
            )
            JobStore.update(
                job_id,
                progress=min(progress, 95),
                message=(
                    f"Procesando bloque {chunk_number}/{total_chunks}: "
                    f"{completed_batches}/{total_batches} lotes aplicados"
                ),
            )

        batch_result = await process_compatibility_batches(
            access_token=access_token,
            user_id=user_id,
            rows=resolved_rows,
            metrics=metrics,
            on_progress=on_batch_progress,
        )

        all_final_rows.extend(batch_result.get("results", []))
        all_batch_results.extend(batch_result.get("batch_results", []))

        completed_rows += len(chunk_rows)
        progress = 10 + int((completed_rows / max(total_rows, 1)) * 85)
        JobStore.update(
            job_id,
            progress=min(progress, 95),
            processed_rows=completed_rows,
            processed_unique_rows=min(total_unique_rows, completed_rows),
            completed_chunks=chunk_number,
            message=f"Bloque {chunk_number}/{total_chunks} completado",
        )

        if chunk_number < total_chunks and pause_seconds > 0:
            JobStore.update(
                job_id,
                progress=min(progress, 95),
                message=(
                    f"Bloque {chunk_number}/{total_chunks} completado. "
                    f"Esperando {format_pause_minutes(pause_seconds)} para continuar."
                ),
            )
            await asyncio.sleep(pause_seconds)

    resolution_summary["unique_rows"] = total_unique_rows
    resolution_summary["duplicated_rows"] = duplicated_rows
    resolution_summary["metrics"] = metrics.to_dict()

    compat_summary = build_compat_summary(all_final_rows, all_batch_results, metrics)
    final_summary = {
        **compat_summary,
        "resolution_summary": resolution_summary,
    }

    JobStore.update(
        job_id,
        progress=100,
        message="Procesamiento finalizado",
        processed_rows=compat_summary.get("processed_rows", 0),
        processed_unique_rows=total_unique_rows,
        summary=final_summary,
        results=all_final_rows,
    )

    logger.info(
        "[ORCHESTRATOR] ======== TOTAL COMPATIBILIDADES CREADAS: %s ========",
        compat_summary.get("total_created_compatibilities", 0),
    )

    return {
        "results": all_final_rows,
        "batch_results": all_batch_results,
        "summary": final_summary,
    }
