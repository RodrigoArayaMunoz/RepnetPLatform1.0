import asyncio
import json
import os
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from services.compatibility_service import JobMetrics, WRITE_RATE_LIMITER, call_ml
from services.excel_service import extract_item_id, normalize_text
from services.job_store import JobStore
from services.ml_client import ml_client


MLC_COLUMN_ALIASES = [
    "MLC",
    "mlc",
    "Mlc",
    "ITEM_ID",
    "item_id",
    "Item ID",
    "ITEM ID",
]

ESTADO_COLUMN_ALIASES = [
    "ESTADO",
    "Estado",
    "estado",
    "STATUS",
    "status",
    "Status",
]

STOCK_COLUMN_ALIASES = [
    "STOCK",
    "Stock",
    "stock",
    "CANTIDAD",
    "cantidad",
    "Cantidad",
]

PRECIO_COLUMN_ALIASES = [
    "PRECIO",
    "Precio",
    "precio",
    "PRICE",
    "price",
    "Price",
]

ESTADO_MAPPING = {
    "activo": "active",
    "activa": "active",
    "active": "active",
    "pausado": "paused",
    "pausada": "paused",
    "paused": "paused",
    "inactivo": "closed",
    "inactiva": "closed",
    "closed": "closed",
    "cerrado": "closed",
    "cerrada": "closed",
}


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


def _resolve_column(df: pd.DataFrame, aliases: list[str], label: str) -> str:
    available = {str(col).strip(): str(col).strip() for col in df.columns}
    for alias in aliases:
        if alias in available:
            return available[alias]
    raise ValueError(
        f"No se encontró la columna {label} en el archivo. "
        f"Columnas aceptadas: {', '.join(aliases)}"
    )


def _parse_estado(raw_value: str) -> str | None:
    if not raw_value:
        return None
    normalized = raw_value.strip().lower()
    return ESTADO_MAPPING.get(normalized)


def _parse_precio(raw_value: Any) -> float | None:
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return None
    try:
        value = float(raw_value)
        if value < 0:
            return None
        return value
    except (TypeError, ValueError):
        return None


def _parse_stock(raw_value: Any) -> int | None:
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return None
    try:
        value = int(float(raw_value))
        if value < 0:
            return None
        return value
    except (TypeError, ValueError):
        return None


def load_price_stock_rows(file_path: str) -> list[dict[str, Any]]:
    df = _load_dataframe(file_path)

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    mlc_column = _resolve_column(df, MLC_COLUMN_ALIASES, "MLC")
    estado_column = _resolve_column(df, ESTADO_COLUMN_ALIASES, "ESTADO")
    stock_column = _resolve_column(df, STOCK_COLUMN_ALIASES, "STOCK")
    precio_column = _resolve_column(df, PRECIO_COLUMN_ALIASES, "PRECIO")

    rows: list[dict[str, Any]] = []

    for index in range(len(df)):
        mlc_raw = normalize_text(df[mlc_column].iloc[index])
        item_id = extract_item_id(mlc_raw)

        estado_raw = normalize_text(df[estado_column].iloc[index])
        estado = _parse_estado(estado_raw)

        stock = _parse_stock(df[stock_column].iloc[index])
        precio = _parse_precio(df[precio_column].iloc[index])

        rows.append(
            {
                "item_id": item_id,
                "mlc_raw": mlc_raw,
                "estado_raw": estado_raw,
                "estado": estado,
                "stock": stock,
                "precio": precio,
                "original_row_index": index,
            }
        )

    return rows


async def _process_price_stock_row(
    access_token: str,
    row: dict[str, Any],
    user_id: str,
    metrics: JobMetrics,
) -> dict[str, Any]:
    item_id = row.get("item_id")
    original_row_index = row.get("original_row_index")
    precio = row.get("precio")
    stock = row.get("stock")
    estado = row.get("estado")
    estado_raw = row.get("estado_raw", "")

    if not item_id:
        return {
            "ok": False,
            "item_id": None,
            "reason": "Fila sin valor válido en la columna MLC",
            "error_code": "MISSING_ITEM_ID",
            "original_row_index": original_row_index,
            "precio": precio,
            "stock": stock,
            "estado": estado,
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

    if estado_raw and estado is None:
        return {
            "ok": False,
            "item_id": item_id,
            "reason": f"Valor de ESTADO no reconocido: '{estado_raw}'. "
                      f"Valores aceptados: Activo/a, Pausado/a, Inactivo/a",
            "error_code": "INVALID_ESTADO",
            "original_row_index": original_row_index,
            "precio": precio,
            "stock": stock,
            "estado": None,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": f"Valor de ESTADO no reconocido: '{estado_raw}'",
                    "error_code": "INVALID_ESTADO",
                    "original_row_index": original_row_index,
                }
            ],
        }

    if precio is None and stock is None and estado is None:
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "No hay datos válidos de PRECIO, STOCK ni ESTADO para actualizar",
            "error_code": "NO_DATA_TO_UPDATE",
            "original_row_index": original_row_index,
            "precio": None,
            "stock": None,
            "estado": None,
            "results": [
                {
                    "ok": False,
                    "item_id": item_id,
                    "reason": "No hay datos válidos para actualizar",
                    "error_code": "NO_DATA_TO_UPDATE",
                    "original_row_index": original_row_index,
                }
            ],
        }

    try:
        response = await call_ml(
            ml_client.update_item_price_stock,
            access_token,
            item_id,
            price=precio,
            available_quantity=stock,
            status=estado,
            user_id=user_id,
            metrics=metrics,
            limiter=WRITE_RATE_LIMITER,
        )

        return {
            "ok": True,
            "item_id": item_id,
            "reason": "Actualizado correctamente",
            "original_row_index": original_row_index,
            "precio": precio,
            "stock": stock,
            "estado": estado,
            "ml_response": response,
            "results": [
                {
                    "ok": True,
                    "item_id": item_id,
                    "reason": "Actualizado correctamente",
                    "original_row_index": original_row_index,
                }
            ],
        }
    except HTTPException as exc:
        return {
            "ok": False,
            "item_id": item_id,
            "reason": str(exc.detail),
            "error_code": f"HTTP_{getattr(exc, 'status_code', 'ERROR')}",
            "original_row_index": original_row_index,
            "precio": precio,
            "stock": stock,
            "estado": estado,
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
            "reason": str(exc),
            "error_code": "UNEXPECTED_EXCEPTION",
            "original_row_index": original_row_index,
            "precio": precio,
            "stock": stock,
            "estado": estado,
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


async def process_price_stock_job(
    job_id: str,
    user_id: str,
    file_path: str,
) -> dict[str, Any]:
    rows = load_price_stock_rows(file_path)
    total_rows = len(rows)
    metrics = JobMetrics()

    JobStore.update(
        job_id,
        status="processing",
        progress=5,
        total_rows=total_rows,
        total_unique_rows=total_rows,
        message="Archivo leído correctamente. Preparando actualización de precios/stock...",
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
            "metrics": metrics.to_dict(),
        }
        return {"results": [], "summary": summary}

    access_token = await ml_client.get_valid_token(int(user_id))

    max_concurrency = max(1, int(getattr(settings, "max_row_concurrency", 2)))
    semaphore = asyncio.Semaphore(max_concurrency)
    progress_lock = asyncio.Lock()
    results: list[dict[str, Any] | None] = [None] * total_rows
    completed = 0

    async def worker(index: int, row: dict[str, Any]) -> None:
        nonlocal completed
        async with semaphore:
            result = await _process_price_stock_row(
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
                    message=f"Actualizando precios/stock {completed}/{total_rows}",
                )

    await asyncio.gather(*(worker(index, row) for index, row in enumerate(rows)))

    final_results = [
        result
        if result is not None
        else {
            "ok": False,
            "item_id": rows[index].get("item_id"),
            "reason": "La fila no devolvió resultado",
            "error_code": "MISSING_RESULT",
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
        "metrics": metrics.to_dict(),
    }

    result_path = os.path.join(settings.upload_dir, f"{job_id}_price_stock_result.json")
    save_json(result_path, final_results)

    JobStore.update(
        job_id,
        status="success",
        progress=100,
        result_path=result_path,
        summary=summary,
        processed_rows=total_rows,
        processed_unique_rows=total_rows,
        message="Actualización de precios y stock finalizada",
    )

    return {"results": final_results, "summary": summary}
