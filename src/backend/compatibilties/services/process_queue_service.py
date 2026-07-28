import asyncio
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import settings
from services.compatibility_exception_service import (
    load_compatibility_exception_rows,
    process_compatibility_exceptions_job,
)
from services.compatibility_orchestrator_service import (
    process_excel_compatibilities_end_to_end,
)
from services.excel_service import COLUMN_ALIASES, load_excel_rows, normalize_for_compare
from services.item_pictures_service import (
    FOTO1_COLUMN_ALIASES as ITEM_PICTURES_FOTO1_COLUMN_ALIASES,
    FOTO2_COLUMN_ALIASES as ITEM_PICTURES_FOTO2_COLUMN_ALIASES,
    FOTO3_COLUMN_ALIASES as ITEM_PICTURES_FOTO3_COLUMN_ALIASES,
    MLC_COLUMN_ALIASES as ITEM_PICTURES_MLC_COLUMN_ALIASES,
    URLS_COLUMN_ALIASES as ITEM_PICTURES_URLS_COLUMN_ALIASES,
    process_item_pictures_job,
)
from services.job_store import JobStore
from services.ml_client import ml_client
from services.price_stock_service import (
    ESTADO_COLUMN_ALIASES,
    MLC_COLUMN_ALIASES as PRICE_STOCK_MLC_COLUMN_ALIASES,
    PRECIO_COLUMN_ALIASES,
    STOCK_COLUMN_ALIASES,
    load_price_stock_rows,
    process_price_stock_job,
)
from services.process_queue_error_store import process_queue_error_store
from services.process_queue_result_store import process_queue_result_store
from services.process_queue_store import process_queue_store
from services.sku_description_service import (
    SKU_LOOKUP_COLUMN_ALIASES as SKU_DESCRIPTION_COLUMN_ALIASES,
    process_sku_description_job,
)
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.supabase_process_store import supabase_process_store

logger = logging.getLogger(__name__)

COMPATIBILITY_QUEUE_REQUIRED_COLUMNS = [
    "ASOCIACION ML",
    "MARCA",
    "MODELO",
    "AÑO",
    "VERSION",
    "CILINDRADA",
    "TRANSMISION",
    "FAMILIA",
    "POSICION_DT",
    "POSICION_ID",
]

PRICE_STOCK_QUEUE_EXTRA_COLUMNS = [
    "canal",
    "sku",
    "tipo_venta",
    "motivo",
]

NO_COMPAT_QUEUE_REQUIRED_COLUMNS = [
    "MLC-NOINFORMADAS",
    "mlc-noinformadas",
    "Mlc-NoInformadas",
]


def _format_queue_delay_message(delay_seconds: int) -> str:
    minutes = delay_seconds // 60
    if delay_seconds > 0 and delay_seconds % 60 == 0 and minutes > 0:
        return f"Esperando {minutes} minutos para ejecutar el siguiente proceso"

    return f"Esperando {delay_seconds} segundos para ejecutar el siguiente proceso"


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _collect_error_messages(detail: Any) -> list[str]:
    messages: list[str] = []

    if detail is None:
        return messages

    if isinstance(detail, str):
        normalized = detail.strip()
        if normalized:
            messages.append(normalized)
        return messages

    if isinstance(detail, list):
        for item in detail:
            messages.extend(_collect_error_messages(item))
        return messages

    if isinstance(detail, dict):
        preferred_keys = ("message", "detail", "reason", "error", "error_message", "msg")
        for key in preferred_keys:
            if key in detail:
                messages.extend(_collect_error_messages(detail.get(key)))

        if not messages:
            for value in detail.values():
                messages.extend(_collect_error_messages(value))

        return messages

    normalized = str(detail).strip()
    if normalized:
        messages.append(normalized)
    return messages


def _build_process_error_payload(
    *,
    exc: Exception,
    process_row_id: int | str | None,
    process_id: str,
    filename: str,
    process_type: str | None,
) -> dict[str, Any]:
    detail = getattr(exc, "detail", None)
    messages = _collect_error_messages(detail)
    fallback_message = str(exc).strip() or "Ocurrió un error no especificado"

    if fallback_message and fallback_message not in messages:
        messages.insert(0, fallback_message)

    return {
        "process_row_id": process_row_id,
        "process_id": process_id,
        "filename": filename,
        "process_type": process_type,
        "error_type": exc.__class__.__name__,
        "message": messages[0] if messages else fallback_message,
        "messages": messages,
        "detail": detail,
        "traceback": traceback.format_exc(),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_partial_process_error_payload(
    *,
    process_row_id: int | str | None,
    process_id: str,
    filename: str,
    process_type: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    failed_items = [
        str(item_id).strip()
        for item_id in (summary.get("failed_item_ids") or [])
        if str(item_id).strip()
    ]
    failed_count = len(failed_items)
    success_count = int(summary.get("success_count") or 0)
    message = (
        f"El proceso terminó con errores en {failed_count} MLC."
        if failed_count
        else "El proceso terminó con errores."
    )

    return {
        "process_row_id": process_row_id,
        "process_id": process_id,
        "filename": filename,
        "process_type": process_type,
        "error_type": "PartialProcessError",
        "message": message,
        "messages": [],
        "failed_items": failed_items,
        "failed_count": failed_count,
        "success_count": success_count,
        "detail": {
            "failed_items": failed_items,
            "failed_count": failed_count,
            "success_count": success_count,
        },
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _display_process_id(process_row: dict[str, Any]) -> str:
    return str(process_row.get("proceso_id") or process_row.get("id") or "")


def _has_partial_process_errors(
    process_type: str | None,
    summary: dict[str, Any],
) -> bool:
    return (
        process_type
        in {
            "compatibilities",
            "price_stock",
            "item_pictures",
            "sku_descriptions",
        }
        and int(summary.get("error_count") or 0) > 0
    )


def _build_extended_partial_process_error_payload(
    *,
    process_row_id: int | str | None,
    process_id: str,
    filename: str,
    process_type: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    failed_items = [
        str(item_id).strip()
        for item_id in (summary.get("failed_item_ids") or [])
        if str(item_id).strip()
    ]
    failed_skus = [
        str(item_sku).strip()
        for item_sku in (summary.get("failed_skus") or [])
        if str(item_sku).strip()
    ]
    failed_count = len(failed_items)
    success_count = int(summary.get("success_count") or 0)

    if failed_count:
        message = f"El proceso terminó con errores en {failed_count} MLC."
    elif failed_skus:
        message = f"El proceso terminó con errores en {len(failed_skus)} SKU."
    else:
        message = "El proceso terminó con errores."

    return {
        "process_row_id": process_row_id,
        "process_id": process_id,
        "filename": filename,
        "process_type": process_type,
        "error_type": "PartialProcessError",
        "message": message,
        "messages": [],
        "failed_items": failed_items,
        "failed_skus": failed_skus,
        "failed_count": failed_count,
        "success_count": success_count,
        "is_partial": True,
        "display_status": "Procesado con Errores",
        "detail": {
            "failed_items": failed_items,
            "failed_skus": failed_skus,
            "failed_count": failed_count,
            "success_count": success_count,
        },
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_aliases(values: list[str]) -> set[str]:
    return {normalize_for_compare(value) for value in values}


def _format_summary_for_log(
    process_type: str | None,
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_summary = summary or {}
    base_summary = {
        "processed_rows": int(normalized_summary.get("processed_rows") or 0),
        "success_count": int(normalized_summary.get("success_count") or 0),
        "error_count": int(normalized_summary.get("error_count") or 0),
        "failed_items": len(normalized_summary.get("failed_item_ids") or []),
    }

    metrics = normalized_summary.get("metrics") or {}
    if metrics:
        base_summary["metrics"] = {
            "duration_seconds": metrics.get("duration_seconds"),
            "ml_requests": metrics.get("ml_requests"),
            "ml_http_errors": metrics.get("ml_http_errors"),
        }

    if process_type == "item_pictures":
        base_summary.update(
            {
                "updated_items": int(normalized_summary.get("updated_items") or 0),
                "picture_sources_total": int(
                    normalized_summary.get("picture_sources_total") or 0
                ),
            }
        )
        return base_summary

    if process_type == "price_stock":
        base_summary.update(
            {
                "items_total": int(
                    normalized_summary.get("items_total")
                    or normalized_summary.get("compatibilities_total")
                    or 0
                ),
                "updated_items": int(
                    normalized_summary.get("updated_items")
                    or normalized_summary.get("compatibilities_ok")
                    or 0
                ),
            }
        )
        return base_summary

    if process_type == "sku_descriptions":
        base_summary.update(
            {
                "sku_total": int(normalized_summary.get("sku_total") or 0),
                "matched_items_total": int(
                    normalized_summary.get("matched_items_total") or 0
                ),
                "exported_rows_total": int(
                    normalized_summary.get("exported_rows_total") or 0
                ),
                "description_errors": int(
                    normalized_summary.get("description_errors") or 0
                ),
            }
        )
        return base_summary

    base_summary.update(
        {
            "compatibilities_total": int(
                normalized_summary.get("compatibilities_total") or 0
            ),
            "compatibilities_ok": int(
                normalized_summary.get("compatibilities_ok") or 0
            ),
            "compatibilities_error": int(
                normalized_summary.get("compatibilities_error") or 0
            ),
        }
    )
    return base_summary


def _build_compatibility_column_aliases() -> dict[str, set[str]]:
    aliases_by_logical: dict[str, set[str]] = {}
    for logical_name in COMPATIBILITY_QUEUE_REQUIRED_COLUMNS:
        aliases_by_logical[logical_name] = _normalize_aliases(
            COLUMN_ALIASES.get(logical_name, [logical_name])
        )
    return aliases_by_logical


COMPATIBILITY_COLUMN_ALIASES = _build_compatibility_column_aliases()
PRICE_STOCK_MLC_ALIASES = _normalize_aliases(PRICE_STOCK_MLC_COLUMN_ALIASES)
PRICE_STOCK_ESTADO_ALIASES = _normalize_aliases(ESTADO_COLUMN_ALIASES)
PRICE_STOCK_STOCK_ALIASES = _normalize_aliases(STOCK_COLUMN_ALIASES)
PRICE_STOCK_PRECIO_ALIASES = _normalize_aliases(PRECIO_COLUMN_ALIASES)
PRICE_STOCK_EXTRA_ALIASES = _normalize_aliases(PRICE_STOCK_QUEUE_EXTRA_COLUMNS)
SKU_DESCRIPTION_ALIASES = _normalize_aliases(SKU_DESCRIPTION_COLUMN_ALIASES)
ITEM_PICTURES_MLC_ALIASES = _normalize_aliases(ITEM_PICTURES_MLC_COLUMN_ALIASES)
ITEM_PICTURES_URLS_ALIASES = _normalize_aliases(ITEM_PICTURES_URLS_COLUMN_ALIASES)
ITEM_PICTURES_FOTO1_ALIASES = _normalize_aliases(ITEM_PICTURES_FOTO1_COLUMN_ALIASES)
ITEM_PICTURES_FOTO2_ALIASES = _normalize_aliases(ITEM_PICTURES_FOTO2_COLUMN_ALIASES)
ITEM_PICTURES_FOTO3_ALIASES = _normalize_aliases(ITEM_PICTURES_FOTO3_COLUMN_ALIASES)
NO_COMPAT_ALIASES = _normalize_aliases(NO_COMPAT_QUEUE_REQUIRED_COLUMNS)


def _load_file_columns(file_path: str) -> set[str]:
    if not os.path.exists(file_path):
        raise ValueError(f"No existe el archivo: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".csv":
            df = pd.read_csv(file_path, nrows=0)
        elif ext in (".xlsx", ".xls"):
            try:
                df = pd.read_excel(
                    file_path,
                    sheet_name="Hoja1",
                    engine="openpyxl",
                    nrows=0,
                )
            except ValueError:
                excel_file = pd.ExcelFile(file_path, engine="openpyxl")
                if not excel_file.sheet_names:
                    raise ValueError("El archivo Excel no contiene hojas")
                df = pd.read_excel(
                    file_path,
                    sheet_name=excel_file.sheet_names[0],
                    engine="openpyxl",
                    nrows=0,
                )
        else:
            raise ValueError("Formato no soportado. Solo se aceptan .xlsx, .xls o .csv")
    except Exception as exc:
        raise ValueError(f"No se pudieron leer las columnas del archivo: {str(exc)}")

    return {
        normalize_for_compare(str(column).strip())
        for column in df.columns
        if str(column).strip()
    }


def _matches_compatibility_columns(columns: set[str]) -> bool:
    return all(
        bool(columns & aliases)
        for aliases in COMPATIBILITY_COLUMN_ALIASES.values()
    )


def _matches_price_stock_columns(columns: set[str]) -> bool:
    mlc_match = columns & PRICE_STOCK_MLC_ALIASES
    precio_match = columns & PRICE_STOCK_PRECIO_ALIASES
    stock_match = columns & PRICE_STOCK_STOCK_ALIASES
    estado_match = columns & PRICE_STOCK_ESTADO_ALIASES
    extra_match = columns & PRICE_STOCK_EXTRA_ALIASES

    logger.info(
        "[PROCESS_QUEUE][DETECT][PRICE_STOCK_CANDIDATE] mlc=%s precio=%s stock=%s estado=%s extra=%s",
        sorted(mlc_match),
        sorted(precio_match),
        sorted(stock_match),
        sorted(estado_match),
        sorted(extra_match),
    )

    if not mlc_match or not precio_match:
        logger.info(
            "[PROCESS_QUEUE][DETECT][PRICE_STOCK_CANDIDATE] rejected missing mlc/precio file_columns=%s",
            sorted(columns),
        )
        return False

    if not (stock_match or estado_match or extra_match):
        logger.info(
            "[PROCESS_QUEUE][DETECT][PRICE_STOCK_CANDIDATE] rejected missing stock/estado/extra columns"
        )
        return False

    logger.info(
        "[PROCESS_QUEUE][DETECT][PRICE_STOCK_CANDIDATE] extra_aliases=%s match=%s",
        sorted(PRICE_STOCK_EXTRA_ALIASES),
        sorted(extra_match),
    )

    return True


def _matches_no_compat_columns(columns: set[str]) -> bool:
    return bool(columns & NO_COMPAT_ALIASES)


def _matches_sku_description_columns(columns: set[str]) -> bool:
    return bool(columns & SKU_DESCRIPTION_ALIASES)


def _matches_item_pictures_columns(columns: set[str]) -> bool:
    has_mlc = bool(columns & ITEM_PICTURES_MLC_ALIASES)
    has_urls = bool(columns & ITEM_PICTURES_URLS_ALIASES)
    has_photo_columns = any(
        bool(columns & aliases)
        for aliases in (
            ITEM_PICTURES_FOTO1_ALIASES,
            ITEM_PICTURES_FOTO2_ALIASES,
            ITEM_PICTURES_FOTO3_ALIASES,
        )
    )
    return has_mlc and (has_urls or has_photo_columns)


def detect_process_type(file_path: str) -> str:
    columns = _load_file_columns(file_path)

    logger.info("[PROCESS_QUEUE][DETECT] file_path=%s columns=%s", file_path, sorted(columns))

    is_compat = _matches_compatibility_columns(columns)
    logger.info("[PROCESS_QUEUE][DETECT] _matches_compatibility_columns=%s", is_compat)
    if is_compat:
        return "compatibilities"

    is_price_stock = _matches_price_stock_columns(columns)
    logger.info("[PROCESS_QUEUE][DETECT] _matches_price_stock_columns=%s", is_price_stock)
    if is_price_stock:
        return "price_stock"

    is_sku_descriptions = _matches_sku_description_columns(columns)
    logger.info(
        "[PROCESS_QUEUE][DETECT] _matches_sku_description_columns=%s",
        is_sku_descriptions,
    )
    if is_sku_descriptions:
        return "sku_descriptions"

    is_item_pictures = _matches_item_pictures_columns(columns)
    logger.info(
        "[PROCESS_QUEUE][DETECT] _matches_item_pictures_columns=%s",
        is_item_pictures,
    )
    if is_item_pictures:
        return "item_pictures"

    is_no_compat = _matches_no_compat_columns(columns)
    logger.info(
        "[PROCESS_QUEUE][DETECT] _matches_no_compat_columns=%s NO_COMPAT_ALIASES=%s",
        is_no_compat, sorted(NO_COMPAT_ALIASES),
    )
    if is_no_compat:
        return "compatibility_exceptions"

    raise ValueError(
        "No se pudo identificar el tipo de proceso por columnas. "
        "Compatibilidades requiere columnas de asociacion, vehiculo, familia y posiciones; "
        "precios/stock requiere mlc y precio_nuevo, y puede incluir stock_nuevo y/o estado_nuevo; "
        "descripciones por SKU requiere una columna SKU-BUSQUEDA o equivalente; "
        "actualizacion de fotos requiere mlc y urls o columnas foto1/foto2/foto3; "
        "no compatibilidades requiere una columna MLC-NOINFORMADAS o equivalente."
    )


async def _run_compatibilities_job(
    *,
    user_id: str,
    file_path: str,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    job = JobStore.create(filename)
    job_id = job["id"]
    JobStore.update(job_id, xlsx_path=file_path)
    process_queue_store.update(current_job_id=job_id)

    try:
        rows = load_excel_rows(file_path)
        access_token = await ml_client.get_valid_token(int(user_id))
        outcome = await process_excel_compatibilities_end_to_end(
            job_id=job_id,
            access_token=access_token,
            user_id=user_id,
            rows=rows,
        )

        result_path = os.path.join(settings.upload_dir, f"{job_id}_queue_compatibilities_result.json")
        _save_json(result_path, outcome.get("results", []))

        JobStore.update(
            job_id,
            status="success",
            progress=100,
            result_path=result_path,
            summary=outcome.get("summary", {}),
            processed_rows=outcome.get("summary", {}).get("processed_rows", 0),
            message="Procesamiento de compatibilidades finalizado desde cola",
        )
        return job_id, outcome.get("summary", {})
    except Exception as exc:
        JobStore.update(
            job_id,
            status="error",
            progress=0,
            message=f"Error ejecutando compatibilidades desde cola: {str(exc)}",
        )
        raise


async def _run_price_stock_job(
    *,
    user_id: str,
    file_path: str,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    job = JobStore.create(filename)
    job_id = job["id"]
    JobStore.update(job_id, xlsx_path=file_path)
    process_queue_store.update(current_job_id=job_id)
    outcome = await process_price_stock_job(job_id=job_id, user_id=user_id, file_path=file_path)
    return job_id, outcome.get("summary", {})


async def _run_compatibility_exceptions_job(
    *,
    user_id: str,
    file_path: str,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    job = JobStore.create(filename)
    job_id = job["id"]
    JobStore.update(job_id, xlsx_path=file_path)
    process_queue_store.update(current_job_id=job_id)
    outcome = await process_compatibility_exceptions_job(
        job_id=job_id,
        user_id=user_id,
        file_path=file_path,
    )
    return job_id, outcome.get("summary", {})


async def _run_sku_descriptions_job(
    *,
    user_id: str,
    file_path: str,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    job = JobStore.create(filename)
    job_id = job["id"]
    JobStore.update(job_id, xlsx_path=file_path)
    process_queue_store.update(current_job_id=job_id)
    outcome = await process_sku_description_job(
        job_id=job_id,
        user_id=user_id,
        file_path=file_path,
    )
    return job_id, outcome.get("summary", {})


async def _run_item_pictures_job(
    *,
    user_id: str,
    file_path: str,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    job = JobStore.create(filename)
    job_id = job["id"]
    JobStore.update(job_id, xlsx_path=file_path)
    process_queue_store.update(current_job_id=job_id)
    outcome = await process_item_pictures_job(
        job_id=job_id,
        user_id=user_id,
        file_path=file_path,
    )
    return job_id, outcome.get("summary", {})


async def _execute_process_record(
    *,
    process_row: dict[str, Any],
    user_id: str,
) -> tuple[str, str, dict[str, Any]]:
    filename = str(process_row.get("archivo") or process_row.get("storage_path") or "proceso.xlsx")
    local_path = await supabase_process_store.download_process_file(
        bucket_name=str(process_row.get("storage_bucket") or ""),
        storage_path=str(process_row.get("storage_path") or ""),
        original_filename=filename,
    )

    try:
        process_type = detect_process_type(local_path)
        process_queue_store.update(current_process_type=process_type)
        logger.info(
            "[PROCESS_QUEUE][DISPATCH] row_id=%s proceso_id=%s filename=%s detected_type=%s",
            process_row.get("id"),
            _display_process_id(process_row),
            filename,
            process_type,
        )

        if process_type == "compatibilities":
            job_id, summary = await _run_compatibilities_job(
                user_id=user_id,
                file_path=local_path,
                filename=filename,
            )
            return process_type, job_id, summary

        if process_type == "price_stock":
            job_id, summary = await _run_price_stock_job(
                user_id=user_id,
                file_path=local_path,
                filename=filename,
            )
            return process_type, job_id, summary

        if process_type == "item_pictures":
            job_id, summary = await _run_item_pictures_job(
                user_id=user_id,
                file_path=local_path,
                filename=filename,
            )
            return process_type, job_id, summary

        if process_type == "sku_descriptions":
            job_id, summary = await _run_sku_descriptions_job(
                user_id=user_id,
                file_path=local_path,
                filename=filename,
            )
            return process_type, job_id, summary

        job_id, summary = await _run_compatibility_exceptions_job(
            user_id=user_id,
            file_path=local_path,
            filename=filename,
        )
        return process_type, job_id, summary
    finally:
        try:
            os.remove(local_path)
        except OSError:
            logger.warning("No se pudo eliminar archivo temporal de cola: %s", local_path)


async def run_process_queue(*, user_id: str) -> None:
    if not supabase_process_store.can_access:
        raise RuntimeError(
            "Supabase no esta configurado para leer la tabla procesos y descargar archivos."
        )

    delay_seconds = max(0, int(getattr(settings, "process_queue_delay_seconds", 1200)))
    completed_count = 0
    last_error: str | None = None
    last_completion_message = ""

    await ml_client.startup()
    await supabase_meli_connection_store.restore_token_store()
    try:
        while True:
            pending_rows = await supabase_process_store.list_pending_processes()
            if not pending_rows:
                process_queue_store.finish(
                    message=(
                        f"Cola finalizada. {last_completion_message}"
                        if last_completion_message
                        else "Cola finalizada. No hay procesos pendientes."
                    ),
                    last_error=last_error,
                )
                return

            current_row = pending_rows[0]
            current_row_id = current_row.get("id")
            current_filename = str(
                current_row.get("archivo")
                or current_row.get("storage_path")
                or "proceso.xlsx"
            )
            current_process_id = _display_process_id(current_row)
            if current_row_id is not None:
                process_queue_result_store.clear(current_row_id)

            process_queue_store.update(
                pending_count=len(pending_rows),
                current_process_row_id=current_row_id,
                current_process_id=current_process_id,
                current_filename=current_filename,
                current_process_type=None,
                next_run_at=None,
                message=f"Procesando {current_filename}",
            )

            try:
                process_type, internal_job_id, summary = await _execute_process_record(
                    process_row=current_row,
                    user_id=user_id,
                )
                has_partial_errors = _has_partial_process_errors(
                    process_type,
                    summary,
                )
                job_data = JobStore.get(internal_job_id) or {}
                result_path = str(job_data.get("result_path") or "").strip()
                has_export_result = (
                    process_type == "sku_descriptions"
                    and bool(result_path)
                    and os.path.exists(result_path)
                    and int(summary.get("exported_rows_total") or 0) > 0
                )

                if has_partial_errors:
                    await supabase_process_store.update_process_status(
                        current_row_id,
                        "Procesado",
                    )
                    if current_row_id is not None:
                        process_queue_error_store.save(
                            current_row_id,
                            _build_extended_partial_process_error_payload(
                                process_row_id=current_row_id,
                                process_id=current_process_id,
                                filename=current_filename,
                                process_type=process_type,
                                summary=summary,
                            ),
                        )
                else:
                    await supabase_process_store.update_process_status(current_row_id, "Procesado")
                    if current_row_id is not None:
                        process_queue_error_store.clear(current_row_id)
                if current_row_id is not None:
                    if has_export_result:
                        process_queue_result_store.save(
                            current_row_id,
                            {
                                "process_row_id": current_row_id,
                                "process_id": current_process_id,
                                "filename": current_filename,
                                "process_type": process_type,
                                "result_path": result_path,
                                "has_export_result": True,
                                "export_kind": "sku_descriptions",
                                "export_label": "Descargar descripciones",
                                "exported_rows_total": int(
                                    summary.get("exported_rows_total") or 0
                                ),
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    else:
                        process_queue_result_store.clear(current_row_id)
                completed_count += 1
                last_error = None
                created_compatibilities = int(
                    summary.get("total_created_compatibilities", 0) or 0
                )
                if process_type == "compatibilities" and has_partial_errors:
                    last_completion_message = (
                        f"{created_compatibilities} compatibilidades agregadas en "
                        f"{current_filename}, con errores en algunas filas."
                    )
                elif process_type == "compatibilities":
                    last_completion_message = (
                        f"{created_compatibilities} compatibilidades "
                        f"agregadas correctamente en {current_filename}."
                    )
                elif has_partial_errors:
                    last_completion_message = (
                        f"Proceso {current_filename} finalizado con errores "
                        "en algunos MLC."
                    )
                else:
                    last_completion_message = (
                        f"Proceso {current_filename} finalizado correctamente."
                    )

                logger.info(
                    "[PROCESS_QUEUE][OK] row_id=%s proceso_id=%s type=%s internal_job_id=%s result=%s",
                    current_row_id,
                    current_process_id,
                    process_type,
                    internal_job_id,
                    _format_summary_for_log(process_type, summary),
                )

                process_queue_store.update(
                    processed_count=completed_count,
                    current_process_type=process_type,
                    message=last_completion_message,
                    last_error=None,
                )
            except Exception as exc:
                completed_count += 1
                error_payload = _build_process_error_payload(
                    exc=exc,
                    process_row_id=current_row_id,
                    process_id=current_process_id,
                    filename=current_filename,
                    process_type=process_queue_store.get_state().get("current_process_type"),
                )
                last_error = error_payload["message"]
                last_completion_message = (
                    f"Error procesando {current_filename}: "
                    f"{error_payload['message']}"
                )
                logger.exception(
                    "[PROCESS_QUEUE][ERROR] row_id=%s proceso_id=%s",
                    current_row_id,
                    current_process_id,
                )
                await supabase_process_store.update_process_status(current_row_id, "Error")
                if current_row_id is not None:
                    process_queue_error_store.save(current_row_id, error_payload)
                    process_queue_result_store.clear(current_row_id)
                process_queue_store.update(
                    processed_count=completed_count,
                    message=last_completion_message,
                    last_error=error_payload["message"],
                )

            remaining_rows = await supabase_process_store.list_pending_processes()
            if not remaining_rows:
                process_queue_store.finish(
                    message=(
                        f"Cola finalizada. {last_completion_message}"
                        if last_completion_message
                        else "Cola finalizada. No hay procesos pendientes."
                    ),
                    last_error=last_error,
                )
                return

            next_run_at = time.time() + delay_seconds
            process_queue_store.update(
                pending_count=len(remaining_rows),
                current_process_row_id=None,
                current_process_id=None,
                current_filename=None,
                current_process_type=None,
                next_run_at=next_run_at,
                message=_format_queue_delay_message(delay_seconds),
            )
            await asyncio.sleep(delay_seconds)
    except Exception as exc:
        process_queue_store.finish(
            message=f"Cola detenida por error: {str(exc)}",
            last_error=str(exc),
        )
        raise
    finally:
        await ml_client.shutdown()
