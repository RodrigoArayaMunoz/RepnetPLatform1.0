import asyncio
import json
import logging
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from services.compatibility_service import (
    COMPATIBILITY_EXCEPTION_WRITE_RATE_LIMITER,
    JobMetrics,
    call_ml,
    get_write_rate_policy,
)
from services.excel_service import extract_item_id, normalize_text
from services.job_store import JobStore
from services.ml_client import ml_client
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_compatibility_exception_chunk_pause_seconds,
    get_process_file_chunk_size,
)

logger = logging.getLogger(__name__)

MLC_COLUMN_ALIASES = [
    "MLC",
    "mlc",
    "Mlc",
    "MLC-NOINFORMADAS",
    "mlc-noinformadas",
    "Mlc-NoInformadas",
    "ITEM_ID",
    "item_id",
    "Item ID",
    "ITEM ID",
]


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_dataframe(file_path: str) -> pd.DataFrame:
    if not os.path.exists(file_path):
        raise ValueError(f"No existe el archivo: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext in (".xlsx", ".xls"):
            try:
                df = pd.read_excel(file_path, sheet_name="Hoja1", engine="openpyxl")
            except ValueError:
                excel_file = pd.ExcelFile(file_path, engine="openpyxl")
                if not excel_file.sheet_names:
                    raise ValueError("El archivo Excel no contiene hojas")
                df = pd.read_excel(
                    file_path,
                    sheet_name=excel_file.sheet_names[0],
                    engine="openpyxl",
                )
        else:
            raise ValueError("Formato no soportado. Solo se aceptan .xlsx, .xls o .csv")
    except Exception as exc:
        raise ValueError(f"No se pudo leer el archivo: {str(exc)}")

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _resolve_mlc_column(df: pd.DataFrame) -> str:
    available = {str(col).strip(): str(col).strip() for col in df.columns}
    for alias in MLC_COLUMN_ALIASES:
        if alias in available:
            return available[alias]
    raise ValueError(
        "No se encontró la columna MLC en el archivo. Columnas aceptadas: "
        + ", ".join(MLC_COLUMN_ALIASES)
    )


def load_compatibility_exception_rows(file_path: str) -> list[dict[str, Any]]:
    df = _load_dataframe(file_path)

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    mlc_column = _resolve_mlc_column(df)
    rows: list[dict[str, Any]] = []

    for index, value in enumerate(df[mlc_column].tolist()):
        item_id = extract_item_id(value)
        rows.append(
            {
                "item_id": item_id,
                "mlc_raw": normalize_text(value),
                "original_row_index": index,
            }
        )

    return rows


async def _process_exception_row(
    access_token: str,
    row: dict[str, Any],
    user_id: str,
    comment: str,
    metrics: JobMetrics,
) -> dict[str, Any]:
    item_id = row.get("item_id")
    original_row_index = row.get("original_row_index")

    if not item_id:
        return {
            "ok": False,
            "item_id": None,
            "brand_name": "Sin marca",
            "model_name": "Sin modelo",
            "reason": "Fila sin valor válido en la columna MLC",
            "error_code": "MISSING_ITEM_ID",
            "comment": comment,
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "item_id": None,
                    "reason": "Fila sin valor válido en la columna MLC",
                    "error_code": "MISSING_ITEM_ID",
                    "original_row_index": original_row_index,
                }
            ],
        }

    try:
        response = await call_ml(
            ml_client.add_item_compatibility_exception,
            access_token,
            item_id,
            comment,
            user_id=user_id,
            metrics=metrics,
            limiter=COMPATIBILITY_EXCEPTION_WRITE_RATE_LIMITER,
        )
        return {
            "ok": True,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Compatibilidad no informada",
            "reason": "Excepción informada correctamente",
            "comment": comment,
            "original_row_index": original_row_index,
            "ml_response": response,
            "results": [
                {
                    "ok": True,
                    "item_id": item_id,
                    "reason": "Excepción informada correctamente",
                    "original_row_index": original_row_index,
                }
            ],
        }
    except HTTPException as exc:
        return {
            "ok": False,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Compatibilidad no informada",
            "reason": str(exc.detail),
            "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
            "comment": comment,
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": str(exc.detail),
                    "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
                    "original_row_index": original_row_index,
                }
            ],
        }
    except Exception as exc:
        return {
            "ok": False,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Compatibilidad no informada",
            "reason": str(exc),
            "error_code": "UNEXPECTED_EXCEPTION",
            "comment": comment,
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": str(exc),
                    "error_code": "UNEXPECTED_EXCEPTION",
                    "original_row_index": original_row_index,
                }
            ],
        }


async def process_compatibility_exceptions_job(
    job_id: str,
    user_id: str,
    file_path: str,
) -> dict[str, Any]:
    rows = load_compatibility_exception_rows(file_path)
    total_rows = len(rows)
    comment = settings.ml_compatibility_exception_comment
    metrics = JobMetrics()
    chunk_size = get_process_file_chunk_size()
    pause_seconds = get_compatibility_exception_chunk_pause_seconds()
    total_chunks = count_chunks(total_rows, chunk_size)

    JobStore.update(
        job_id,
        status="processing",
        progress=5,
        total_rows=total_rows,
        total_unique_rows=total_rows,
        total_chunks=total_chunks,
        completed_chunks=0,
        message="Preparando archivo para informar excepciones...",
    )

    if total_rows == 0:
        summary = {
            "processed_rows": 0,
            "unique_rows": 0,
            "success_count": 0,
            "error_count": 0,
            "compatibilities_total": 0,
            "compatibilities_ok": 0,
            "compatibilities_error": 0,
            "comment_used": comment,
            "metrics": metrics.to_dict(),
        }
        return {"results": [], "summary": summary}

    access_token = await ml_client.get_valid_token(int(user_id))

    max_concurrency = max(1, int(getattr(settings, "max_row_concurrency", 2)))
    write_policy = get_write_rate_policy()

    logger.info(
        "[COMPATIBILITY_EXCEPTION][POLICY] chunk_size=%s pause_seconds=%s max_concurrency=%s requests_per_second=%.4f max_requests_per_window=%s window_seconds=%s cooldown_seconds=%s",
        chunk_size,
        pause_seconds,
        max_concurrency,
        write_policy["requests_per_second"],
        write_policy["max_requests_per_window"],
        write_policy["window_seconds"],
        write_policy["cooldown_seconds"],
    )

    progress_lock = asyncio.Lock()
    results: list[dict[str, Any] | None] = [None] * total_rows
    completed = 0
    indexed_rows = list(enumerate(rows))

    async def worker(
        index: int,
        row: dict[str, Any],
        *,
        chunk_number: int,
        total_chunks_count: int,
    ) -> None:
        nonlocal completed
        result = await _process_exception_row(
            access_token=access_token,
            row=row,
            user_id=user_id,
            comment=comment,
            metrics=metrics,
        )
        results[index] = result

        async with progress_lock:
            completed += 1
            progress = 10 + int((completed / max(total_rows, 1)) * 85)
            JobStore.update(
                job_id,
                progress=min(progress, 95),
                processed_rows=completed,
                processed_unique_rows=completed,
                message=f"Procesando archivo: {completed}/{total_rows} filas informadas",
            )

    for chunk_number, (_, chunk_entries) in enumerate(
        chunk_sequence(indexed_rows, chunk_size),
        start=1,
    ):
        JobStore.update(
            job_id,
            message=f"Procesando bloque {chunk_number}/{total_chunks}...",
        )

        semaphore = asyncio.Semaphore(max_concurrency)

        async def chunk_worker(index: int, row: dict[str, Any]) -> None:
            async with semaphore:
                await worker(
                    index,
                    row,
                    chunk_number=chunk_number,
                    total_chunks_count=total_chunks,
                )

        await asyncio.gather(
            *(chunk_worker(index, row) for index, row in chunk_entries)
        )

        JobStore.update(
            job_id,
            completed_chunks=chunk_number,
            message=f"Bloque {chunk_number}/{total_chunks} completado",
        )

        if chunk_number < total_chunks and pause_seconds > 0:
            JobStore.update(
                job_id,
                message=(
                    f"Bloque {chunk_number}/{total_chunks} completado. "
                    f"Esperando {format_pause_minutes(pause_seconds)} para continuar."
                ),
            )
            await asyncio.sleep(pause_seconds)

    final_results = [
        result
        if result is not None
        else {
            "ok": False,
            "item_id": rows[index].get("item_id"),
            "brand_name": "Mercado Libre",
            "model_name": "Compatibilidad no informada",
            "reason": "La fila no devolvió resultado",
            "error_code": "MISSING_RESULT",
            "comment": comment,
            "original_row_index": rows[index].get("original_row_index"),
            "results": [],
        }
        for index, result in enumerate(results)
    ]

    success_count = sum(1 for row in final_results if row.get("ok"))
    error_count = total_rows - success_count
    unique_items = len({row.get("item_id") for row in final_results if row.get("item_id")})

    summary = {
        "processed_rows": total_rows,
        "unique_rows": unique_items,
        "success_count": success_count,
        "error_count": error_count,
        "compatibilities_total": total_rows,
        "compatibilities_ok": success_count,
        "compatibilities_error": error_count,
        "comment_used": comment,
        "metrics": metrics.to_dict(),
    }

    result_path = os.path.join(settings.upload_dir, f"{job_id}_compatibility_exceptions_result.json")
    save_json(result_path, final_results)

    JobStore.update(
        job_id,
        status="success",
        progress=100,
        result_path=result_path,
        summary=summary,
        processed_rows=total_rows,
        processed_unique_rows=total_rows,
        message="Carga de excepciones de compatibilidad finalizada",
    )

    return {"results": final_results, "summary": summary}
