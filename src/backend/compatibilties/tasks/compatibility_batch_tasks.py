import asyncio
import json
import os

from celery.utils.log import get_task_logger

from celery_app import celery_app
from config import settings
from services.compatibility_batch_service import process_compatibility_batches
from services.job_store import JobStore
from services.ml_client import ml_client

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
            message="Cargando archivo resuelto...",
        )

        rows = load_json(resolved_path)
        logger.info("[TASK BATCH] Filas cargadas=%s", len(rows))

        async def on_progress(completed: int, total: int) -> None:
            progress = 10 + int((completed / max(total, 1)) * 85)
            JobStore.update(
                job_id,
                progress=min(progress, 95),
                processed_rows=completed,
                message=f"Procesando batches {completed}/{total}",
            )

        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))
            logger.info("[TASK BATCH] Token válido obtenido")

            outcome = await process_compatibility_batches(
                access_token=access_token,
                user_id=int(user_id),
                rows=rows,
                on_progress=on_progress,
            )
        finally:
            await ml_client.shutdown()

        logger.info("[TASK BATCH] Summary=%s", outcome["summary"])

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
            batch_debug_path=batch_debug_path,
            message="Carga batch de compatibilidades finalizada",
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