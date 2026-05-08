import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

from celery import chord

from celery_app import celery_app
from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_service import (
    JobMetrics,
    build_results_summary,
    build_unique_rows_plan,
    call_ml,
    process_unique_rows_chunk,
)
from services.excel_service import load_excel_rows
from services.job_store import JobStore
from services.ml_client import ml_client


CHUNK_SIZE = max(1, int(getattr(settings, "excel_chunk_size", 1000)))


def save_json(path: str, data: Any) -> None:
    """
    Escritura atómica para evitar archivos JSON corruptos o incompletos.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(temp_path, path)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def chunk_list(items: list, chunk_size: int) -> list[list]:
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def sum_metrics_dicts(*metrics_dicts: dict | None) -> dict:
    keys = [
        "ml_requests",
        "ml_retries",
        "ml_rate_limited",
        "ml_http_errors",
        "ml_technical_errors",
        "cache_hits",
        "cache_misses",
    ]

    result = {key: 0 for key in keys}

    for metrics in metrics_dicts:
        if not isinstance(metrics, dict):
            continue

        for key in keys:
            result[key] += int(metrics.get(key, 0) or 0)

    return result


def sum_chunk_metrics(chunk_outputs: list[dict]) -> dict:
    result = {
        "ml_requests": 0,
        "ml_retries": 0,
        "ml_rate_limited": 0,
        "ml_http_errors": 0,
        "ml_technical_errors": 0,
        "cache_hits": 0,
        "cache_misses": 0,
    }

    for output in chunk_outputs:
        summary = output.get("summary", {})
        metrics = summary.get("metrics", {})
        if not isinstance(metrics, dict):
            continue

        for key in result.keys():
            result[key] += int(metrics.get(key, 0) or 0)

    return result


@celery_app.task(name="tasks.process_excel_job")
def process_excel_job(job_id: str, user_id: str) -> None:
    asyncio.run(_dispatch_excel_job(job_id, user_id))


async def _dispatch_excel_job(job_id: str, user_id: str) -> None:
    job = JobStore.get(job_id)
    if not job:
        return

    try:
        JobStore.update(
            job_id,
            status="processing",
            message="Preparando procesamiento por chunks...",
            progress=1,
        )

        xlsx_path = job.get("xlsx_path")
        if not xlsx_path:
            raise ValueError("El job no tiene xlsx_path asociado")

        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(f"No existe el archivo Excel: {xlsx_path}")

        JobStore.update(job_id, progress=3, message="Leyendo archivo Excel...")
        rows = load_excel_rows(xlsx_path)

        total_rows = len(rows)
        if total_rows == 0:
            raise ValueError("El archivo no contiene filas válidas para procesar")

        unique_entries, original_indices_by_unique_index = build_unique_rows_plan(rows)
        total_unique_rows = len(unique_entries)
        duplicated_rows = total_rows - total_unique_rows

        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))

            JobStore.update(
                job_id,
                progress=5,
                message="Precargando diccionarios globales desde Mercado Libre...",
            )

            catalog_metrics = JobMetrics()
            catalog_cache = CatalogPreloadService(
                call_ml=call_ml,
                metrics=catalog_metrics,
            )
            catalog_data = await catalog_cache.preload_all(
                access_token,
                user_id=user_id,
            )
            catalog_snapshot = catalog_cache.to_snapshot()
            catalog_metrics_payload = catalog_metrics.to_dict()
        finally:
            await ml_client.shutdown()

        job_dir = os.path.join(settings.upload_dir, job_id)
        chunk_dir = os.path.join(job_dir, "chunks")
        partial_dir = os.path.join(job_dir, "partials")
        os.makedirs(chunk_dir, exist_ok=True)
        os.makedirs(partial_dir, exist_ok=True)

        catalog_snapshot_path = os.path.join(job_dir, "catalog_snapshot.json")
        meta_path = os.path.join(job_dir, "meta.json")

        save_json(catalog_snapshot_path, catalog_snapshot)

        chunks = chunk_list(unique_entries, CHUNK_SIZE)

        save_json(
            meta_path,
            {
                "job_id": job_id,
                "started_at": time.time(),
                "total_rows": total_rows,
                "total_unique_rows": total_unique_rows,
                "duplicated_rows": duplicated_rows,
                "original_indices_by_unique_index": original_indices_by_unique_index,
                "catalog_stats": catalog_data.stats() if hasattr(catalog_data, "stats") else {},
                "catalog_preload_metrics": catalog_metrics_payload,
                "chunk_count": len(chunks),
            },
        )

        if not chunks:
            summary = build_results_summary(
                [],
                total_rows=0,
                total_unique_rows=0,
                duplicated_rows=0,
                metrics=catalog_metrics_payload,
            )

            result_path = os.path.join(settings.upload_dir, f"{job_id}_resultado.json")
            save_json(result_path, [])

            JobStore.update(
                job_id,
                status="success",
                message="No se generaron chunks para procesar",
                progress=100,
                processed_rows=0,
                processed_unique_rows=0,
                completed_chunks=0,
                total_chunks=0,
                result_path=result_path,
                summary=summary,
                results_count=0,
            )
            return

        chunk_paths: list[str] = []
        for chunk_index, chunk in enumerate(chunks):
            chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index:04d}.json")
            save_json(chunk_path, chunk)
            chunk_paths.append(chunk_path)

        JobStore.initialize_chunk_plan(
            job_id,
            total_rows=total_rows,
            total_unique_rows=total_unique_rows,
            total_chunks=len(chunk_paths),
            message=(
                f"Se generaron {len(chunk_paths)} chunks para "
                f"{total_unique_rows} filas únicas"
            ),
        )

        header = [
            process_excel_chunk.si(
                job_id,
                user_id,
                chunk_index,
                chunk_path,
                catalog_snapshot_path,
                total_unique_rows,
            )
            for chunk_index, chunk_path in enumerate(chunk_paths)
        ]

        result = chord(header)(finalize_excel_job.s(job_id, meta_path))

        JobStore.update(
            job_id,
            task_id=result.id,
            status="processing",
            progress=10,
            message=f"Procesamiento distribuido iniciado con {len(chunk_paths)} chunks",
        )

    except Exception as exc:
        JobStore.update(
            job_id,
            status="error",
            message=f"Error preparando procesamiento: {str(exc)}",
            progress=0,
        )
        raise


@celery_app.task(name="tasks.process_excel_chunk")
def process_excel_chunk(
    job_id: str,
    user_id: str,
    chunk_index: int,
    chunk_path: str,
    catalog_snapshot_path: str,
    total_unique_rows: int,
) -> dict:
    return asyncio.run(
        _process_excel_chunk(
            job_id,
            user_id,
            chunk_index,
            chunk_path,
            catalog_snapshot_path,
            total_unique_rows,
        )
    )


async def _process_excel_chunk(
    job_id: str,
    user_id: str,
    chunk_index: int,
    chunk_path: str,
    catalog_snapshot_path: str,
    total_unique_rows: int,
) -> dict:
    try:
        chunk_entries = load_json(chunk_path)
        catalog_snapshot = load_json(catalog_snapshot_path)

        reported_completed = 0

        async def on_chunk_progress(current_completed: int) -> None:
            nonlocal reported_completed

            delta = current_completed - reported_completed
            if delta <= 0:
                return

            JobStore.increment_processed_unique_rows(
                job_id,
                delta=delta,
                total_unique_rows=total_unique_rows,
            )
            reported_completed = current_completed

        progress_callback: Callable[[int], Awaitable[None]] = on_chunk_progress

        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))
            catalog_cache = CatalogPreloadService.from_snapshot(catalog_snapshot)

            outcome = await process_unique_rows_chunk(
                access_token=access_token,
                user_id=user_id,
                unique_entries=chunk_entries,
                catalog_cache=catalog_cache,
                on_progress=progress_callback,
            )
        finally:
            await ml_client.shutdown()

        partial_dir = os.path.join(settings.upload_dir, job_id, "partials")
        partial_path = os.path.join(partial_dir, f"chunk_{chunk_index:04d}_result.json")
        save_json(partial_path, outcome)

        JobStore.mark_chunk_completed(job_id)

        return {
            "chunk_index": chunk_index,
            "partial_result_path": partial_path,
            "processed_unique_rows": len(chunk_entries),
            "summary": outcome.get("summary", {}),
        }

    except Exception as exc:
        JobStore.update(
            job_id,
            status="error",
            message=f"Error en chunk {chunk_index}: {str(exc)}",
        )
        raise


@celery_app.task(name="tasks.finalize_excel_job")
def finalize_excel_job(chunk_outputs: list[dict], job_id: str, meta_path: str) -> None:
    try:
        meta = load_json(meta_path)

        total_rows = int(meta["total_rows"])
        total_unique_rows = int(meta["total_unique_rows"])
        duplicated_rows = int(meta["duplicated_rows"])
        original_indices_by_unique_index = meta["original_indices_by_unique_index"]
        catalog_preload_metrics = meta.get("catalog_preload_metrics", {})
        catalog_stats = meta.get("catalog_stats", {})

        unique_results_by_index: dict[int, dict] = {}

        for chunk_output in chunk_outputs:
            partial_path = chunk_output.get("partial_result_path")
            if not partial_path or not os.path.exists(partial_path):
                continue

            partial = load_json(partial_path)

            for result in partial.get("results", []):
                unique_index = int(result["unique_index"])
                unique_results_by_index[unique_index] = result

        final_results: list[dict | None] = [None] * total_rows

        for unique_index in range(total_unique_rows):
            result = unique_results_by_index.get(unique_index) or {
                "ok": False,
                "reason": "Resultado faltante del chunk",
                "error_type": "technical",
                "error_code": "MISSING_CHUNK_RESULT",
                "results": [],
            }

            original_indices = original_indices_by_unique_index[unique_index]

            for original_idx in original_indices:
                copied_result = dict(result)
                copied_result["source_row_index"] = unique_index
                copied_result["original_row_index"] = original_idx
                copied_result["was_duplicated"] = len(original_indices) > 1
                final_results[original_idx] = copied_result

        final_results = [
            r if r is not None else {
                "ok": False,
                "reason": "Resultado final faltante",
                "error_type": "technical",
                "error_code": "MISSING_FINAL_RESULT",
                "results": [],
            }
            for r in final_results
        ]

        chunk_metrics = sum_chunk_metrics(chunk_outputs)
        aggregated_metrics = sum_metrics_dicts(catalog_preload_metrics, chunk_metrics)
        aggregated_metrics["wall_duration_seconds"] = round(
            time.time() - float(meta.get("started_at", time.time())),
            2,
        )
        aggregated_metrics["chunks_total"] = len(chunk_outputs)
        aggregated_metrics["catalog_stats"] = catalog_stats

        summary = build_results_summary(
            final_results,
            total_rows=total_rows,
            total_unique_rows=total_unique_rows,
            duplicated_rows=duplicated_rows,
            metrics=aggregated_metrics,
        )

        result_path = os.path.join(settings.upload_dir, f"{job_id}_resultado.json")
        save_json(result_path, final_results)

        JobStore.update(
            job_id,
            status="success",
            message="Procesamiento finalizado correctamente",
            progress=100,
            processed_rows=total_rows,
            processed_unique_rows=total_unique_rows,
            completed_chunks=len(chunk_outputs),
            result_path=result_path,
            summary=summary,
            results_count=len(final_results),
        )

    except Exception as exc:
        JobStore.update(
            job_id,
            status="error",
            message=f"Error finalizando procesamiento: {str(exc)}",
        )
        raise
