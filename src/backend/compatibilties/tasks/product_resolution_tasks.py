import asyncio
import json
import os

from celery_app import celery_app
from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_service import JobMetrics, call_ml
from services.excel_service import load_excel_rows
from services.job_store import JobStore
from services.ml_client import ml_client
from services.product_resolution_service import resolve_products_from_rows


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@celery_app.task(name="tasks.resolve_products_job")
def resolve_products_job(job_id: str, user_id: str, site_id: str = "MLC") -> None:
    asyncio.run(_resolve_products_job(job_id, user_id, site_id))


async def _resolve_products_job(job_id: str, user_id: str, site_id: str) -> None:
    job = JobStore.get(job_id)
    if not job:
        return

    xlsx_path = job.get("xlsx_path")
    if not xlsx_path or not os.path.exists(xlsx_path):
        JobStore.update(job_id, status="error", message="Excel no encontrado", progress=0)
        return

    try:
        JobStore.update(job_id, status="processing", progress=1, message="Leyendo Excel...")

        rows = load_excel_rows(xlsx_path)
        await ml_client.startup()
        try:
            access_token = await ml_client.get_valid_token(int(user_id))

            metrics = JobMetrics()
            catalog_cache = CatalogPreloadService(call_ml=call_ml, metrics=metrics)
            await catalog_cache.preload_all(access_token)

            JobStore.update(job_id, progress=10, message="Resolviendo product_id...")
            outcome = await resolve_products_from_rows(
                job_id=job_id,
                access_token=access_token,
                site_id=site_id,
                rows=rows,
                catalog_cache=catalog_cache,
            )
        finally:
            await ml_client.shutdown()

        result_path = os.path.join(settings.upload_dir, f"{job_id}_resolved_products.json")
        save_json(result_path, outcome["rows"])

        JobStore.update(
            job_id,
            status="success",
            progress=100,
            result_path=result_path,
            processed_rows=len(outcome["rows"]),
            summary=outcome["summary"],
            message="Resolución de product_id finalizada",
        )
    except Exception as exc:
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error resolviendo product_id: {str(exc)}",
        )
        raise