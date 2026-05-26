import asyncio
import json
import logging
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from services.compatibility_service import (
    ITEM_PICTURES_WRITE_RATE_LIMITER,
    JobMetrics,
    call_ml,
    get_write_rate_policy,
)
from services.excel_service import extract_item_id
from services.job_store import JobStore
from services.ml_client import ml_client
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_item_pictures_chunk_pause_seconds,
    get_process_file_chunk_size,
)

logger = logging.getLogger(__name__)


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

FOTO1_COLUMN_ALIASES = [
    "FOTO1",
    "Foto1",
    "foto1",
    "FOTO 1",
    "Foto 1",
    "foto 1",
    "LINK1",
    "Link1",
    "link1",
    "LINK 1",
    "Link 1",
    "link 1",
]

FOTO2_COLUMN_ALIASES = [
    "FOTO2",
    "Foto2",
    "foto2",
    "FOTO 2",
    "Foto 2",
    "foto 2",
    "LINK2",
    "Link2",
    "link2",
    "LINK 2",
    "Link 2",
    "link 2",
]

FOTO3_COLUMN_ALIASES = [
    "FOTO3",
    "Foto3",
    "foto3",
    "FOTO 3",
    "Foto 3",
    "foto 3",
    "LINK3",
    "Link3",
    "link3",
    "LINK 3",
    "Link 3",
    "link 3",
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


def _resolve_optional_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    available = {str(column).strip(): str(column).strip() for column in df.columns}
    for alias in aliases:
        if alias in available:
            return available[alias]
    return None


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


def _resolve_picture_columns(df: pd.DataFrame) -> tuple[str | None, list[str]]:
    urls_column = _resolve_optional_column(df, URLS_COLUMN_ALIASES)
    photo_columns = [
        resolved_column
        for resolved_column in (
            _resolve_optional_column(df, FOTO1_COLUMN_ALIASES),
            _resolve_optional_column(df, FOTO2_COLUMN_ALIASES),
            _resolve_optional_column(df, FOTO3_COLUMN_ALIASES),
        )
        if resolved_column
    ]

    return urls_column, photo_columns


def _collect_picture_urls(
    df: pd.DataFrame,
    index: int,
    *,
    urls_column: str | None,
    photo_columns: list[str],
) -> list[str]:
    picture_urls: list[str] = []

    if urls_column:
        picture_urls.extend(_parse_picture_urls(df[urls_column].iloc[index]))

    for photo_column in photo_columns:
        photo_url = _cell_to_text(df[photo_column].iloc[index])
        if photo_url:
            picture_urls.append(photo_url)

    return picture_urls


def load_item_picture_rows(file_path: str) -> list[dict[str, Any]]:
    df = _load_dataframe(file_path)

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    mlc_column = _resolve_column(df, MLC_COLUMN_ALIASES, "MLC")
    urls_column, photo_columns = _resolve_picture_columns(df)

    if not urls_column and not photo_columns:
        accepted_columns = [
            *URLS_COLUMN_ALIASES,
            *FOTO1_COLUMN_ALIASES,
            *FOTO2_COLUMN_ALIASES,
            *FOTO3_COLUMN_ALIASES,
        ]
        raise ValueError(
            "No se encontró ninguna columna válida de fotos en el archivo. "
            f"Columnas aceptadas: {', '.join(accepted_columns)}"
        )

    rows: list[dict[str, Any]] = []

    for index in range(len(df)):
        mlc_raw = _cell_to_text(df[mlc_column].iloc[index])
        picture_urls = _collect_picture_urls(
            df,
            index,
            urls_column=urls_column,
            photo_columns=photo_columns,
        )

        rows.append(
            {
                "item_id": extract_item_id(mlc_raw),
                "mlc_raw": mlc_raw,
                "picture_urls": picture_urls,
                "pictures_count": len(picture_urls),
                "picture_columns_detected": [
                    *(["URLS"] if urls_column else []),
                    *photo_columns,
                ],
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
            "reason": "No se encontraron fotos válidas en las columnas configuradas",
            "error_code": "NO_PICTURES_TO_UPDATE",
            "pictures_count": 0,
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": "No se encontraron fotos válidas en las columnas configuradas",
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
            limiter=ITEM_PICTURES_WRITE_RATE_LIMITER,
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
    pause_seconds = get_item_pictures_chunk_pause_seconds()
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
            "process_type": "item_pictures",
            "processed_rows": 0,
            "unique_rows": 0,
            "success_count": 0,
            "error_count": 0,
            "failed_item_ids": [],
            "items_total": 0,
            "updated_items": 0,
            "picture_update_errors": 0,
            "picture_sources_total": 0,
            "compatibilities_total": 0,
            "compatibilities_ok": 0,
            "compatibilities_error": 0,
            "metrics": metrics.to_dict(),
        }
        return {"results": [], "summary": summary}

    access_token = await ml_client.get_valid_token(int(user_id))
    max_concurrency = max(1, int(getattr(settings, "max_row_concurrency", 2)))
    write_policy = get_write_rate_policy()

    logger.info(
        "[ITEM_PICTURES][POLICY] chunk_size=%s pause_seconds=%s max_concurrency=%s requests_per_second=%.4f max_requests_per_window=%s window_seconds=%s cooldown_seconds=%s",
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
    successful_updates = 0
    indexed_rows = list(enumerate(rows))

    async def worker(
        index: int,
        row: dict[str, Any],
        *,
        chunk_number: int,
        total_chunks_count: int,
    ) -> None:
        nonlocal completed, successful_updates
        result = await _process_item_picture_row(
            access_token=access_token,
            row=row,
            user_id=user_id,
            metrics=metrics,
        )
        results[index] = result

        async with progress_lock:
            completed += 1
            if result.get("ok"):
                successful_updates += 1
                if successful_updates % 100 == 0:
                    logger.info(
                        "[ITEM_PICTURES][SUCCESS_COUNTER] job_id=%s updated_items=%s processed_rows=%s total_rows=%s",
                        job_id,
                        successful_updates,
                        completed,
                        total_rows,
                    )
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

    total_picture_sources = sum(int(row.get("pictures_count") or 0) for row in final_results)

    summary = {
        "process_type": "item_pictures",
        "processed_rows": total_rows,
        "unique_rows": unique_items,
        "success_count": success_count,
        "error_count": error_count,
        "failed_item_ids": failed_item_ids,
        "items_total": total_rows,
        "updated_items": success_count,
        "picture_update_errors": error_count,
        "picture_sources_total": total_picture_sources,
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

    logger.info(
        "[ITEM_PICTURES][SUMMARY] job_id=%s processed_rows=%s updated_items=%s errors=%s picture_sources=%s",
        job_id,
        total_rows,
        success_count,
        error_count,
        total_picture_sources,
    )

    return {"results": final_results, "summary": summary}
