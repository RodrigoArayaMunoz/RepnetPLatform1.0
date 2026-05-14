import asyncio
import json
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from services.compatibility_service import (
    JobMetrics,
    PRICE_STOCK_WRITE_RATE_LIMITER,
    call_ml,
)
from services.excel_service import extract_item_id
from services.job_store import JobStore
from services.ml_client import ml_client
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_price_stock_chunk_pause_seconds,
    get_process_file_chunk_size,
)


MLC_COLUMN_ALIASES = [
    "MLC",
    "mlc",
    "Mlc",
    "ITEM_ID",
    "item_id",
    "Item ID",
    "ITEM ID",
]

URLS_COLUMN_ALIASES = [
    "URLS",
    "urls",
    "Urls",
    "URL",
    "url",
    "Url",
]


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


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
    df.columns = [str(column).strip() for column in df.columns]
    return df


def _resolve_column(df: pd.DataFrame, aliases: list[str], label: str) -> str:
    available = {str(column).strip(): str(column).strip() for column in df.columns}
    for alias in aliases:
        if alias in available:
            return available[alias]
    raise ValueError(
        f"No se encontró la columna {label} en el archivo. "
        f"Columnas aceptadas: {', '.join(aliases)}"
    )


def _cell_to_text(raw_value: Any) -> str:
    if raw_value is None:
        return ""

    try:
        if pd.isna(raw_value):
            return ""
    except TypeError:
        pass

    return str(raw_value).strip()


def _parse_picture_urls(raw_value: Any) -> list[str]:
    text = _cell_to_text(raw_value)
    if not text:
        return []

    return [part.strip() for part in text.split("|||") if part and part.strip()]


def load_item_picture_rows(file_path: str) -> list[dict[str, Any]]:
    df = _load_dataframe(file_path)

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    mlc_column = _resolve_column(df, MLC_COLUMN_ALIASES, "MLC")
    urls_column = _resolve_column(df, URLS_COLUMN_ALIASES, "URLS")

    rows: list[dict[str, Any]] = []

    for index in range(len(df)):
        mlc_raw = _cell_to_text(df[mlc_column].iloc[index])
        picture_urls = _parse_picture_urls(df[urls_column].iloc[index])

        rows.append(
            {
                "item_id": extract_item_id(mlc_raw),
                "mlc_raw": mlc_raw,
                "picture_urls": picture_urls,
                "pictures_count": len(picture_urls),
                "original_row_index": index,
            }
        )

    return rows


async def _process_item_picture_row(
    *,
    access_token: str,
    row: dict[str, Any],
    user_id: str,
    metrics: JobMetrics,
) -> dict[str, Any]:
    item_id = row.get("item_id")
    original_row_index = row.get("original_row_index")
    picture_urls = row.get("picture_urls") or []
    pictures_count = int(row.get("pictures_count") or 0)

    if not item_id:
        return {
            "ok": False,
            "item_id": None,
            "brand_name": "Mercado Libre",
            "model_name": "Actualización de fotos",
            "reason": "Fila sin valor válido en la columna MLC",
            "error_code": "MISSING_ITEM_ID",
            "pictures_count": pictures_count,
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

    if not picture_urls:
        return {
            "ok": False,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Actualización de fotos",
            "reason": "No se encontraron URLs válidas en la columna URLS",
            "error_code": "NO_PICTURES_TO_UPDATE",
            "pictures_count": 0,
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": "No se encontraron URLs válidas en la columna URLS",
                    "error_code": "NO_PICTURES_TO_UPDATE",
                    "original_row_index": original_row_index,
                }
            ],
        }

    try:
        response = await call_ml(
            ml_client.update_item_pictures,
            access_token,
            item_id,
            picture_urls=picture_urls,
            user_id=user_id,
            metrics=metrics,
            limiter=PRICE_STOCK_WRITE_RATE_LIMITER,
        )

        return {
            "ok": True,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Actualización de fotos",
            "reason": "Fotos actualizadas correctamente",
            "pictures_count": pictures_count,
            "original_row_index": original_row_index,
            "ml_response": response,
            "results": [
                {
                    "ok": True,
                    "item_id": item_id,
                    "reason": "Fotos actualizadas correctamente",
                    "original_row_index": original_row_index,
                }
            ],
        }
    except HTTPException as exc:
        return {
            "ok": False,
            "item_id": item_id,
            "brand_name": "Mercado Libre",
            "model_name": "Actualización de fotos",
            "reason": str(exc.detail),
            "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
            "pictures_count": pictures_count,
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
            "model_name": "Actualización de fotos",
            "reason": str(exc),
            "error_code": "UNEXPECTED_EXCEPTION",
            "pictures_count": pictures_count,
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


async def process_item_pictures_job(
    job_id: str,
    user_id: str,
    file_path: str,
) -> dict[str, Any]:
    rows = load_item_picture_rows(file_path)
    total_rows = len(rows)
    metrics = JobMetrics()
    chunk_size = get_process_file_chunk_size()
    pause_seconds = get_price_stock_chunk_pause_seconds()
    total_chunks = count_chunks(total_rows, chunk_size)

    JobStore.update(
        job_id,
        status="processing",
        progress=5,
        total_rows=total_rows,
        total_unique_rows=total_rows,
        total_chunks=total_chunks,
        completed_chunks=0,
        message="Preparando archivo para actualizar fotos...",
    )

    if total_rows == 0:
        summary = {
            "processed_rows": 0,
            "unique_rows": 0,
            "success_count": 0,
            "error_count": 0,
            "failed_item_ids": [],
            "compatibilities_total": 0,
            "compatibilities_ok": 0,
            "compatibilities_error": 0,
            "metrics": metrics.to_dict(),
        }
        return {"results": [], "summary": summary}

    access_token = await ml_client.get_valid_token(int(user_id))
    max_concurrency = max(1, int(getattr(settings, "max_row_concurrency", 2)))
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
        result = await _process_item_picture_row(
            access_token=access_token,
            row=row,
            user_id=user_id,
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
                message=f"Procesando archivo: {completed}/{total_rows} filas actualizadas",
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
            "model_name": "Actualización de fotos",
            "reason": "La fila no devolvió resultado",
            "error_code": "MISSING_RESULT",
            "pictures_count": rows[index].get("pictures_count", 0),
            "original_row_index": rows[index].get("original_row_index"),
            "results": [],
        }
        for index, result in enumerate(results)
    ]

    success_count = sum(1 for row in final_results if row.get("ok"))
    error_count = total_rows - success_count
    unique_items = len({row.get("item_id") for row in final_results if row.get("item_id")})
    failed_item_ids: list[str] = []
    seen_failed_item_ids: set[str] = set()

    for row in final_results:
        if row.get("ok"):
            continue

        item_id = str(row.get("item_id") or "").strip()
        if not item_id or item_id in seen_failed_item_ids:
            continue

        seen_failed_item_ids.add(item_id)
        failed_item_ids.append(item_id)

    summary = {
        "processed_rows": total_rows,
        "unique_rows": unique_items,
        "success_count": success_count,
        "error_count": error_count,
        "failed_item_ids": failed_item_ids,
        "compatibilities_total": total_rows,
        "compatibilities_ok": success_count,
        "compatibilities_error": error_count,
        "metrics": metrics.to_dict(),
    }

    result_path = os.path.join(settings.upload_dir, f"{job_id}_item_pictures_result.json")
    save_json(result_path, final_results)

    JobStore.update(
        job_id,
        status="success",
        progress=100,
        result_path=result_path,
        summary=summary,
        processed_rows=total_rows,
        processed_unique_rows=total_rows,
        message="Actualización de fotos finalizada",
    )

    return {"results": final_results, "summary": summary}
