import asyncio
import json
import os

from celery_app import celery_app
from config import settings
from services.compatibility_batch_service import process_compatibility_batches
from services.job_store import JobStore
from services.ml_client import ml_client


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
    print(
        f"[TASK BATCH] add_compatibilities_batch_job iniciado "
        f"job_id={job_id} user_id={user_id} resolved_path={resolved_path}",
        flush=True,
    )
    asyncio.run(_add_compatibilities_batch_job(job_id, user_id, resolved_path))


async def _add_compatibilities_batch_job(job_id: str, user_id: str, resolved_path: str) -> None:
    if not os.path.exists(resolved_path):
        print(
            f"[TASK BATCH][ERROR] Archivo resuelto no encontrado: {resolved_path}",
            flush=True,
        )
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

        print(f"[TASK BATCH] Cargando archivo: {resolved_path}", flush=True)
        rows = load_json(resolved_path)
        print(f"[TASK BATCH] Filas cargadas: {len(rows)}", flush=True)

        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))
            print("[TASK BATCH] Token válido obtenido", flush=True)

            outcome = await process_compatibility_batches(
                access_token=access_token,
                rows=rows,
            )
        finally:
            await ml_client.shutdown()

        print(f"[TASK BATCH] Summary: {outcome['summary']}", flush=True)

        result_path = os.path.join(settings.upload_dir, f"{job_id}_compat_batch_result.json")
        save_json(result_path, outcome["results"])

        JobStore.update(
            job_id,
            status="success",
            progress=100,
            result_path=result_path,
            summary=outcome["summary"],
            processed_rows=len(rows),
            message="Carga batch de compatibilidades finalizada",
        )

        print(
            f"[TASK BATCH][OK] Finalizado job_id={job_id} result_path={result_path}",
            flush=True,
        )

    except Exception as exc:
        print(f"[TASK BATCH][ERROR] {str(exc)}", flush=True)
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error agregando compatibilidades batch: {str(exc)}",
        )
        raise