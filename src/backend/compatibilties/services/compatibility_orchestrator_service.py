from services.compatibility_batch_service import process_compatibility_batches
from services.compatibility_service import process_rows_for_job
from services.job_store import JobStore


async def process_excel_compatibilities_end_to_end(
    *,
    job_id: str,
    access_token: str,
    user_id: int | str,
    rows: list[dict],
) -> dict:
    JobStore.update(
        job_id,
        progress=1,
        message="Iniciando resolución de product_id por vehículo único...",
    )

    resolution_result = await process_rows_for_job(
        job_id=job_id,
        access_token=access_token,
        rows=rows,
    )

    resolved_rows = resolution_result.get("results", [])
    resolution_summary = resolution_result.get("summary", {})

    JobStore.update(
        job_id,
        progress=66,
        message=(
            "Etapa 2/2 - Agregando compatibilidades batch: "
            "preparando grupos por ITEM ID"
        ),
        processed_rows=resolution_summary.get("processed_rows", 0),
        summary={
            "resolution": resolution_summary,
        },
    )

    async def on_batch_progress(completed: int, total: int) -> None:
        progress = 66 + int((completed / max(total, 1)) * 29)
        JobStore.update(
            job_id,
            progress=min(progress, 95),
            message=f"Etapa 2/2 - Agregando compatibilidades batch: Procesando batches {completed}/{total}",
        )

    batch_result = await process_compatibility_batches(
        access_token=access_token,
        user_id=user_id,
        rows=resolved_rows,
        on_progress=on_batch_progress,
    )

    final_summary = batch_result.get("summary", {})
    final_result = {
        "results": batch_result.get("results", []),
        "batch_results": batch_result.get("batch_results", []),
        "summary": {
            **final_summary,
            "resolution_summary": resolution_summary,
        },
    }

    JobStore.update(
        job_id,
        progress=100,
        message="Procesamiento finalizado",
        processed_rows=final_summary.get("processed_rows", 0),
        summary=final_result["summary"],
        results=final_result["results"],
    )

    
    return final_result