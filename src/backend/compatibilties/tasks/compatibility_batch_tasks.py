import asyncio
import json
import os

from celery.utils.log import get_task_logger

from celery_app import celery_app
from config import settings
from services.compatibility_batch_service import (
    build_compat_summary,
    process_compatibility_batches,
)
from services.compatibility_service import JobMetrics
from services.job_store import JobStore
from services.ml_client import ml_client
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_compatibility_chunk_pause_seconds,
    get_process_file_chunk_size,
)

logger = get_task_logger(__name__)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@celery_app.task(name="tasks.add_compatibilities_batch_job")
def add_compatibilities_batch_job(job_id: str, user_id: str, resolved_path: str) -> None:
    logger.info(
        "[TASK BATCH][START] job_id=%s user_id=%s resolved_path=%s",
        job_id,
        user_id,
        resolved_path,
    )
    asyncio.run(_add_compatibilities_batch_job(job_id, user_id, resolved_path))


async def _add_compatibilities_batch_job(job_id: str, user_id: str, resolved_path: str) -> None:
    if not os.path.exists(resolved_path):
        logger.error("[TASK BATCH][ERROR] Archivo resuelto no encontrado: %s", resolved_path)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message="Archivo resuelto no encontrado",
        )
        return

    try:
        JobStore.update(
            job_id,
            status="processing",
            progress=1,
            compatibilities_created=0,
            message="Cargando archivo resuelto...",
        )

        rows = load_json(resolved_path)
        logger.info("[TASK BATCH] Filas cargadas=%s", len(rows))
        logger.info(
            "[TASK BATCH] Item IDs únicos=%s",
            len({str(r.get('item_id') or '') for r in rows if r.get('item_id')})
        )
        logger.info(
            "[TASK BATCH] Familias de vehículos presentes=%s",
            len(
                {
                    str(r.get("product_family_key") or "")
                    for r in rows
                    if r.get("product_family_key")
                }
            ),
        )

        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))
            logger.info("[TASK BATCH] Token válido obtenido")

            chunk_size = get_process_file_chunk_size()
            pause_seconds = get_compatibility_chunk_pause_seconds()
            total_chunks = count_chunks(len(rows), chunk_size)
            metrics = JobMetrics()
            all_results: list[dict] = []
            all_batch_results: list[dict] = []
            processed_rows = 0
            created_compatibilities = 0

            for chunk_number, (_, chunk_rows) in enumerate(
                chunk_sequence(rows, chunk_size),
                start=1,
            ):
                created_before_chunk = created_compatibilities

                async def on_progress(
                    completed: int,
                    total: int,
                    chunk_created_compatibilities: int,
                ) -> None:
                    local_ratio = completed / max(total, 1)
                    completed_equivalent = processed_rows + int(
                        local_ratio * len(chunk_rows)
                    )
                    progress = 10 + int(
                        (completed_equivalent / max(len(rows), 1)) * 85
                    )
                    current_created = (
                        created_before_chunk
                        + chunk_created_compatibilities
                    )
                    JobStore.update(
                        job_id,
                        progress=min(progress, 95),
                        processed_rows=processed_rows,
                        compatibilities_created=current_created,
                        message=(
                            f"Procesando bloque {chunk_number}/{total_chunks}: "
                            f"{completed}/{total} lotes · "
                            f"{current_created} compatibilidades agregadas"
                        ),
                    )

                chunk_outcome = await process_compatibility_batches(
                    access_token=access_token,
                    user_id=user_id,
                    rows=chunk_rows,
                    metrics=metrics,
                    on_progress=on_progress,
                )
                all_results.extend(chunk_outcome.get("results", []))
                all_batch_results.extend(
                    chunk_outcome.get("batch_results", [])
                )
                created_compatibilities += int(
                    chunk_outcome.get("summary", {}).get(
                        "total_created_compatibilities",
                        0,
                    )
                    or 0
                )
                processed_rows += len(chunk_rows)

                if chunk_number < total_chunks and pause_seconds > 0:
                    JobStore.update(
                        job_id,
                        processed_rows=processed_rows,
                        compatibilities_created=created_compatibilities,
                        message=(
                            f"Bloque {chunk_number}/{total_chunks} completado. "
                            f"{created_compatibilities} compatibilidades agregadas. "
                            f"Esperando {format_pause_minutes(pause_seconds)} "
                            "para continuar."
                        ),
                    )
                    await asyncio.sleep(pause_seconds)

            outcome = {
                "results": all_results,
                "batch_results": all_batch_results,
                "summary": build_compat_summary(
                    all_results,
                    all_batch_results,
                    metrics,
                ),
            }
        finally:
            await ml_client.shutdown()

        logger.info("[TASK BATCH] Summary=%s", outcome["summary"])
        logger.info(
            "[TASK BATCH] ======== TOTAL COMPATIBILIDADES CREADAS: %s ========",
            outcome["summary"].get("total_created_compatibilities", 0),
        )

        result_path = os.path.join(settings.upload_dir, f"{job_id}_compat_batch_result.json")
        save_json(result_path, outcome["results"])

        batch_debug_path = os.path.join(settings.upload_dir, f"{job_id}_compat_batch_debug.json")
        save_json(batch_debug_path, outcome["batch_results"])

        JobStore.update(
            job_id,
            status="success",
            progress=100,
            result_path=result_path,
            summary=outcome["summary"],
            processed_rows=outcome["summary"].get("processed_rows", len(rows)),
            compatibilities_created=outcome["summary"].get(
                "total_created_compatibilities",
                0,
            ),
            batch_debug_path=batch_debug_path,
            message=(
                "Carga batch de compatibilidades finalizada · "
                f"{outcome['summary'].get('total_created_compatibilities', 0)} "
                "compatibilidades agregadas"
            ),
        )

        logger.info("[TASK BATCH][OK] job_id=%s result_path=%s", job_id, result_path)

    except Exception as exc:
        logger.exception("[TASK BATCH][ERROR] job_id=%s", job_id)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error agregando compatibilidades batch: {str(exc)}",
        )
        raise
