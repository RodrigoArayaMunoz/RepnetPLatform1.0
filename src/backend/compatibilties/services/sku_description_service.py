import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pandas as pd
from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Font

from config import settings
from services.compatibility_service import JobMetrics, READ_RATE_LIMITER, call_ml
from services.excel_service import extract_item_id, normalize_for_compare
from services.job_store import JobStore
from services.ml_client import ml_client
from services.process_chunking_service import (
    chunk_sequence,
    count_chunks,
    format_pause_minutes,
    get_process_file_chunk_size,
    get_sku_description_chunk_pause_seconds,
)

logger = logging.getLogger(__name__)


SKU_LOOKUP_COLUMN_ALIASES = [
    "SKU-BUSQUEDA",
    "Sku-Busqueda",
    "sku-busqueda",
    "SKU BUSQUEDA",
    "Sku Busqueda",
    "sku busqueda",
    "SKU_BUSQUEDA",
    "Sku_Busqueda",
    "sku_busqueda",
]


@dataclass
class SkuDescriptionCaches:
    search_results: dict[str, list[str]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)


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


def load_sku_description_rows(file_path: str) -> list[dict[str, Any]]:
    df = _load_dataframe(file_path)

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    sku_column = _resolve_column(df, SKU_LOOKUP_COLUMN_ALIASES, "SKU-BUSQUEDA")
    rows: list[dict[str, Any]] = []

    for index in range(len(df)):
        rows.append(
            {
                "seller_sku": _cell_to_text(df[sku_column].iloc[index]),
                "original_row_index": index,
            }
        )

    return rows


def _normalize_item_ids(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []

    normalized_items: list[str] = []
    seen_items: set[str] = set()

    for raw_item in raw_items:
        item_id = extract_item_id(raw_item)
        if not item_id or item_id in seen_items:
            continue
        seen_items.add(item_id)
        normalized_items.append(item_id)

    return normalized_items


def _extract_description_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("plain_text", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value.strip()
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            for key in ("plain_text", "text"):
                value = snapshot.get(key)
                if isinstance(value, str):
                    return value.strip()
    return ""


async def _search_item_ids_by_sku(
    *,
    access_token: str,
    seller_sku: str,
    user_id: str,
    caches: SkuDescriptionCaches,
    metrics: JobMetrics,
) -> list[str]:
    cache_key = normalize_for_compare(seller_sku)
    if cache_key in caches.search_results:
        metrics.cache_hits += 1
        return caches.search_results[cache_key]

    metrics.cache_misses += 1
    payload = await call_ml(
        ml_client.search_items_by_seller_sku,
        access_token,
        user_id,
        seller_sku,
        user_id=user_id,
        metrics=metrics,
        limiter=READ_RATE_LIMITER,
    )

    item_ids = _normalize_item_ids(payload.get("results") if isinstance(payload, dict) else payload)
    caches.search_results[cache_key] = item_ids
    return item_ids


async def _get_description_by_item_id(
    *,
    access_token: str,
    item_id: str,
    user_id: str,
    caches: SkuDescriptionCaches,
    metrics: JobMetrics,
) -> str:
    if item_id in caches.descriptions:
        metrics.cache_hits += 1
        return caches.descriptions[item_id]

    metrics.cache_misses += 1
    try:
        payload = await call_ml(
            ml_client.get_item_description,
            access_token,
            item_id,
            user_id=user_id,
            metrics=metrics,
            limiter=READ_RATE_LIMITER,
        )
        description = _extract_description_text(payload)
    except HTTPException as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        description = ""

    caches.descriptions[item_id] = description
    return description


async def _process_sku_description_row(
    *,
    access_token: str,
    row: dict[str, Any],
    user_id: str,
    caches: SkuDescriptionCaches,
    metrics: JobMetrics,
) -> dict[str, Any]:
    seller_sku = str(row.get("seller_sku") or "").strip()
    original_row_index = row.get("original_row_index")

    if not seller_sku:
        return {
            "ok": False,
            "seller_sku": "",
            "item_ids": [],
            "matched_count": 0,
            "export_rows": [],
            "item_errors": [],
            "failed_item_ids": [],
            "reason": "Fila sin valor válido en la columna SKU-BUSQUEDA",
            "error_code": "MISSING_SELLER_SKU",
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "seller_sku": "",
                    "reason": "Fila sin valor válido en la columna SKU-BUSQUEDA",
                    "error_code": "MISSING_SELLER_SKU",
                    "original_row_index": original_row_index,
                }
            ],
        }

    try:
        item_ids = await _search_item_ids_by_sku(
            access_token=access_token,
            seller_sku=seller_sku,
            user_id=user_id,
            caches=caches,
            metrics=metrics,
        )
    except HTTPException as exc:
        return {
            "ok": False,
            "seller_sku": seller_sku,
            "item_ids": [],
            "matched_count": 0,
            "export_rows": [],
            "item_errors": [],
            "failed_item_ids": [],
            "reason": str(exc.detail),
            "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "seller_sku": seller_sku,
                    "reason": str(exc.detail),
                    "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
                    "original_row_index": original_row_index,
                }
            ],
        }
    except Exception as exc:
        return {
            "ok": False,
            "seller_sku": seller_sku,
            "item_ids": [],
            "matched_count": 0,
            "export_rows": [],
            "item_errors": [],
            "failed_item_ids": [],
            "reason": str(exc),
            "error_code": "UNEXPECTED_EXCEPTION",
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "seller_sku": seller_sku,
                    "reason": str(exc),
                    "error_code": "UNEXPECTED_EXCEPTION",
                    "original_row_index": original_row_index,
                }
            ],
        }

    if not item_ids:
        return {
            "ok": False,
            "seller_sku": seller_sku,
            "item_ids": [],
            "matched_count": 0,
            "export_rows": [],
            "item_errors": [],
            "failed_item_ids": [],
            "reason": "No se encontraron publicaciones para ese seller_sku",
            "error_code": "SKU_WITHOUT_ITEMS",
            "original_row_index": original_row_index,
            "results": [
                {
                    "ok": False,
                    "seller_sku": seller_sku,
                    "reason": "No se encontraron publicaciones para ese seller_sku",
                    "error_code": "SKU_WITHOUT_ITEMS",
                    "original_row_index": original_row_index,
                }
            ],
        }

    export_rows: list[dict[str, str]] = []
    item_errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for item_id in item_ids:
        try:
            description = await _get_description_by_item_id(
                access_token=access_token,
                item_id=item_id,
                user_id=user_id,
                caches=caches,
                metrics=metrics,
            )
            export_rows.append(
                {
                    "sku": seller_sku,
                    "item_id": item_id,
                    "description": description,
                }
            )
            results.append(
                {
                    "ok": True,
                    "seller_sku": seller_sku,
                    "item_id": item_id,
                    "reason": "Descripción obtenida correctamente",
                    "original_row_index": original_row_index,
                }
            )
        except HTTPException as exc:
            reason = str(exc.detail)
            item_errors.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
                }
            )
            results.append(
                {
                    "ok": False,
                    "seller_sku": seller_sku,
                    "item_id": item_id,
                    "reason": reason,
                    "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
                    "original_row_index": original_row_index,
                }
            )
        except Exception as exc:
            reason = str(exc)
            item_errors.append(
                {
                    "item_id": item_id,
                    "reason": reason,
                    "error_code": "UNEXPECTED_EXCEPTION",
                }
            )
            results.append(
                {
                    "ok": False,
                    "seller_sku": seller_sku,
                    "item_id": item_id,
                    "reason": reason,
                    "error_code": "UNEXPECTED_EXCEPTION",
                    "original_row_index": original_row_index,
                }
            )

    matched_count = len(export_rows)
    failed_item_ids = [
        str(item_error.get("item_id") or "").strip()
        for item_error in item_errors
        if str(item_error.get("item_id") or "").strip()
    ]
    ok = matched_count > 0 and len(item_errors) == 0

    if matched_count == 0:
        reason = "No se pudieron obtener descripciones válidas para los MLC encontrados"
        error_code = "ITEMS_WITHOUT_DESCRIPTIONS"
    elif item_errors:
        reason = (
            f"Se encontraron {matched_count} descripciones, "
            f"pero hubo {len(item_errors)} MLC con error"
        )
        error_code = "PARTIAL_ITEM_DESCRIPTION_ERRORS"
    else:
        reason = f"Se obtuvieron {matched_count} descripciones correctamente"
        error_code = None

    return {
        "ok": ok,
        "seller_sku": seller_sku,
        "item_ids": item_ids,
        "matched_count": matched_count,
        "export_rows": export_rows,
        "item_errors": item_errors,
        "failed_item_ids": failed_item_ids,
        "reason": reason,
        "error_code": error_code,
        "original_row_index": original_row_index,
        "results": results,
    }


def flatten_sku_description_export_rows(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    flattened_rows: list[dict[str, str]] = []

    for row in results:
        export_rows = row.get("export_rows")
        if not isinstance(export_rows, list):
            continue

        for export_row in export_rows:
            if not isinstance(export_row, dict):
                continue
            flattened_rows.append(
                {
                    "SKU": str(export_row.get("sku") or "").strip(),
                    "MLC": str(export_row.get("item_id") or "").strip(),
                    "DESCRIPCION": str(export_row.get("description") or ""),
                }
            )

    return flattened_rows


def build_sku_description_excel(results: list[dict[str, Any]]) -> BytesIO:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "SKU Descripciones"
    headers = ["SKU", "MLC", "DESCRIPCION"]

    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    for export_row in flatten_sku_description_export_rows(results):
        worksheet.append(
            [
                export_row["SKU"],
                export_row["MLC"],
                export_row["DESCRIPCION"],
            ]
        )

    worksheet.column_dimensions["A"].width = 28
    worksheet.column_dimensions["B"].width = 22
    worksheet.column_dimensions["C"].width = 90

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


async def process_sku_description_job(
    job_id: str,
    user_id: str,
    file_path: str,
) -> dict[str, Any]:
    rows = load_sku_description_rows(file_path)
    total_rows = len(rows)
    metrics = JobMetrics()
    caches = SkuDescriptionCaches()
    chunk_size = get_process_file_chunk_size()
    pause_seconds = get_sku_description_chunk_pause_seconds()
    total_chunks = count_chunks(total_rows, chunk_size)

    JobStore.update(
        job_id,
        status="processing",
        progress=5,
        total_rows=total_rows,
        total_unique_rows=total_rows,
        total_chunks=total_chunks,
        completed_chunks=0,
        message="Preparando archivo para buscar descripciones por SKU...",
    )

    if total_rows == 0:
        summary = {
            "process_type": "sku_descriptions",
            "processed_rows": 0,
            "unique_rows": 0,
            "success_count": 0,
            "error_count": 0,
            "failed_item_ids": [],
            "failed_skus": [],
            "sku_total": 0,
            "rows_with_matches": 0,
            "rows_without_matches": 0,
            "matched_items_total": 0,
            "exported_rows_total": 0,
            "description_errors": 0,
            "metrics": metrics.to_dict(),
        }
        return {"results": [], "summary": summary}

    access_token = await ml_client.get_valid_token(int(user_id))
    max_concurrency = max(1, int(getattr(settings, "max_row_concurrency", 2)))

    logger.info(
        "[SKU_DESCRIPTIONS][POLICY] chunk_size=%s pause_seconds=%s max_concurrency=%s read_requests_per_second=%.4f",
        chunk_size,
        pause_seconds,
        max_concurrency,
        float(getattr(settings, "ml_read_requests_per_second", 0.8)),
    )

    progress_lock = asyncio.Lock()
    results: list[dict[str, Any] | None] = [None] * total_rows
    completed = 0
    indexed_rows = list(enumerate(rows))

    async def worker(index: int, row: dict[str, Any]) -> None:
        nonlocal completed
        result = await _process_sku_description_row(
            access_token=access_token,
            row=row,
            user_id=user_id,
            caches=caches,
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
                message=f"Procesando archivo: {completed}/{total_rows} filas analizadas",
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
                await worker(index, row)

        await asyncio.gather(*(chunk_worker(index, row) for index, row in chunk_entries))

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
            "seller_sku": rows[index].get("seller_sku"),
            "item_ids": [],
            "matched_count": 0,
            "export_rows": [],
            "item_errors": [],
            "failed_item_ids": [],
            "reason": "La fila no devolvió resultado",
            "error_code": "MISSING_RESULT",
            "original_row_index": rows[index].get("original_row_index"),
            "results": [],
        }
        for index, result in enumerate(results)
    ]

    success_count = sum(1 for row in final_results if row.get("ok"))
    error_count = total_rows - success_count
    unique_skus = len(
        {
            normalize_for_compare(row.get("seller_sku"))
            for row in final_results
            if str(row.get("seller_sku") or "").strip()
        }
    )
    failed_item_ids: list[str] = []
    seen_failed_item_ids: set[str] = set()
    failed_skus: list[str] = []
    seen_failed_skus: set[str] = set()
    matched_items_total = 0
    rows_with_matches = 0
    description_errors = 0

    for row in final_results:
        matched_count = int(row.get("matched_count") or 0)
        matched_items_total += matched_count
        if matched_count > 0:
            rows_with_matches += 1

        description_errors += len(row.get("item_errors") or [])

        if row.get("ok"):
            continue

        seller_sku = str(row.get("seller_sku") or "").strip()
        if seller_sku:
            seller_sku_key = normalize_for_compare(seller_sku)
            if seller_sku_key not in seen_failed_skus:
                seen_failed_skus.add(seller_sku_key)
                failed_skus.append(seller_sku)

        for item_id in row.get("failed_item_ids") or []:
            normalized_item_id = str(item_id).strip()
            if not normalized_item_id or normalized_item_id in seen_failed_item_ids:
                continue
            seen_failed_item_ids.add(normalized_item_id)
            failed_item_ids.append(normalized_item_id)

    export_rows = flatten_sku_description_export_rows(final_results)
    summary = {
        "process_type": "sku_descriptions",
        "processed_rows": total_rows,
        "unique_rows": unique_skus,
        "success_count": success_count,
        "error_count": error_count,
        "failed_item_ids": failed_item_ids,
        "failed_skus": failed_skus,
        "sku_total": total_rows,
        "rows_with_matches": rows_with_matches,
        "rows_without_matches": total_rows - rows_with_matches,
        "matched_items_total": matched_items_total,
        "exported_rows_total": len(export_rows),
        "description_errors": description_errors,
        "metrics": metrics.to_dict(),
    }

    result_path = os.path.join(settings.upload_dir, f"{job_id}_sku_descriptions_result.json")
    save_json(result_path, final_results)

    JobStore.update(
        job_id,
        status="success",
        progress=100,
        result_path=result_path,
        summary=summary,
        processed_rows=total_rows,
        processed_unique_rows=total_rows,
        message="Búsqueda de descripciones por SKU finalizada",
    )

    logger.info(
        "[SKU_DESCRIPTIONS][SUMMARY] job_id=%s processed_rows=%s exported_rows=%s errors=%s matched_items_total=%s",
        job_id,
        total_rows,
        len(export_rows),
        error_count,
        matched_items_total,
    )

    return {"results": final_results, "summary": summary}
