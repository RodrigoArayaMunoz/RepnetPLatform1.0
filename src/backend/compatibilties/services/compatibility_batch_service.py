import asyncio
import json
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Iterable

from fastapi import HTTPException

from config import settings
from services.compatibility_service import JobMetrics, WRITE_RATE_LIMITER, call_ml
from services.ml_client import ml_client
from services.product_cache_service import ProductCacheService

logger = logging.getLogger(__name__)


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value) -> str:
    return _safe_text(value).lower()


# ── Mapeo de value_id para restricciones ──────────────────────────
POSICION_DT_VALUE_IDS = {
    "delantera": "13701104",
    "trasera": "13701105",
    "conductor": "13373175",
    "acompañante": "13373176",
}

POSICION_ID_VALUE_IDS = {
    "izquierda": "2262158",
    "derecha": "2262160",
}

FAMILIAS_BRAKE_SHOCK = {"MLC-VEHICLE_BRAKE_PADS", "MLC-VEHICLE_SHOCK_ABSORBERS"}
FAMILIAS_LIGHTS = {"MLC-VEHICLE_TAIL_LIGHTS", "MLC-VEHICLE_HEADLIGHTS"}
FAMILIAS_BRAKE_DISC = {"MLC-VEHICLE_BRAKE_DISCS"}


def build_restrictions(familia: str, posicion_dt: str, posicion_id: str) -> list:
    """Construye la lista `restrictions` para el body del endpoint."""
    familia_upper = _safe_text(familia).upper()
    if not familia_upper:
        return []

    dt_lower = _safe_text(posicion_dt).lower()
    id_lower = _safe_text(posicion_id).lower()

    dt_value_id = POSICION_DT_VALUE_IDS.get(dt_lower, "")
    id_value_id = POSICION_ID_VALUE_IDS.get(id_lower, "")
    dt_name = _safe_text(posicion_dt)
    id_name = _safe_text(posicion_id)

    if familia_upper in FAMILIAS_BRAKE_SHOCK:
        return [
            {
                "attribute_id": "POSITION",
                "attribute_values": [
                    {
                        "values": [
                            {"value_id": POSICION_ID_VALUE_IDS.get("izquierda", ""), "value_name": "Izquierda"},
                            {"value_id": dt_value_id, "value_name": dt_name},
                        ]
                    },
                    {
                        "values": [
                            {"value_id": POSICION_ID_VALUE_IDS.get("derecha", ""), "value_name": "Derecha"},
                            {"value_id": dt_value_id, "value_name": dt_name},
                        ]
                    },
                ],
            }
        ]

    if familia_upper in FAMILIAS_LIGHTS:
        return [
            {
                "attribute_id": "POSITION",
                "attribute_values": [
                    {
                        "values": [
                            {"value_id": dt_value_id, "value_name": dt_name},
                            {"value_id": id_value_id, "value_name": id_name},
                        ]
                    }
                ],
            }
        ]
    
    if familia_upper in FAMILIAS_BRAKE_DISC:
        return  [
            {
                "attribute_id": "POSITION",
                "attribute_values": [
                    {
                        "values": [
                            {"value_id": dt_value_id, "value_name": dt_name},
                        ]
                    }
                ],
            }
        ]
        #print("=" * 60)
        #print(f"[BRAKE_DISC][RESTRICTION] familia={familia_upper} posicion_dt={dt_name}")
        #print(f"[BRAKE_DISC][RESTRICTION] JSON:\n{json.dumps(restriction, indent=2, ensure_ascii=False)}")
        #print("=" * 60)
        #logger.info(
            #"[BRAKE_DISC][RESTRICTION] familia=%s posicion_dt=%s restriction=%s",
            #familia_upper, dt_name, json.dumps(restriction, ensure_ascii=False),
        #)
        return restriction

    #return []


def chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _error_type_for_status(status_code: int | None) -> str:
    if status_code is not None and 400 <= status_code < 500:
        return "functional"
    return "technical"


def _pick_error_text(payload: dict) -> str | None:
    for key in ("message", "detail", "error"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_http_error_message(detail) -> str:
    if isinstance(detail, dict):
        return _pick_error_text(detail) or json.dumps(detail, ensure_ascii=False)

    if not isinstance(detail, str):
        return str(detail)

    first_brace = detail.find("{")
    if first_brace >= 0:
        try:
            parsed = json.loads(detail[first_brace:])
            if isinstance(parsed, dict):
                return _pick_error_text(parsed) or detail
        except json.JSONDecodeError:
            pass

    return detail


async def get_item_compact_cached(
    *,
    access_token: str,
    user_id: int | str,
    item_id: str,
    metrics: JobMetrics,
) -> dict:
    cached = ProductCacheService.get_item_compact(item_id)
    if cached:
        logger.info("[BATCH][CACHE_HIT] item_id=%s", item_id)
        return cached

    logger.info("[BATCH][CACHE_MISS] item_id=%s -> consultando item detail", item_id)
    item_detail = await call_ml(
        ml_client.get_item_detail,
        access_token,
        item_id,
        user_id=user_id,
        metrics=metrics,
    )

    compact = {
        "item_id": item_id,
        "category_id": item_detail.get("category_id"),
        "user_product_id": item_detail.get("user_product_id"),
    }
    ProductCacheService.set_item_compact(item_id, compact)
    return compact


def build_grouped_product_ids(rows: list[dict]) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Agrupa product_ids por item_id y captura datos de restricción del primer row."""
    grouped: dict[str, set[str]] = defaultdict(set)
    restriction_data: dict[str, dict] = {}

    for row in rows:
        item_id = row.get("item_id")
        product_id = row.get("product_id")
        ok = row.get("ok")

        if not item_id or not product_id or not ok:
            continue

        item_key = str(item_id)
        grouped[item_key].add(str(product_id))

        if item_key not in restriction_data:
            restriction_data[item_key] = {
                "familia": _safe_text(row.get("familia", "")),
                "posicion_dt": _safe_text(row.get("posicion_dt", "")),
                "posicion_id": _safe_text(row.get("posicion_id", "")),
            }

    sorted_grouped = {
        item_id: sorted(list(product_ids))
        for item_id, product_ids in grouped.items()
    }
    return sorted_grouped, restriction_data

async def post_compatibilities_batch(
    *,
    access_token: str,
    user_id: int | str,
    item_id: str,
    product_ids: list[str],
    restrictions: list | None = None,
    metrics: JobMetrics,
) -> dict:
    batch_size = min(200, max(1, int(getattr(settings, "compat_batch_size", 200))))
    product_ids = [str(pid) for pid in product_ids][:batch_size]

    try:
        item_compact = await get_item_compact_cached(
            access_token=access_token,
            user_id=user_id,
            item_id=item_id,
            metrics=metrics,
        )

        category_id = item_compact.get("category_id")
        user_product_id = item_compact.get("user_product_id")

        if not category_id or not user_product_id:
            logger.error(
                "[BATCH][ERROR] item_id=%s sin category_id o user_product_id",
                item_id,
            )
            return {
                "ok": False,
                "item_id": item_id,
                "product_ids": product_ids,
                "error_type": "technical",
                "error_code": "MISSING_ITEM_DATA",
                "error_message": "No se obtuvo category_id o user_product_id",
                "response": None,
            }

        logger.info(
            "[BATCH][PUT] item_id=%s user_product_id=%s products_sent=%s restrictions=%s",
            item_id,
            user_product_id,
            len(product_ids),
            restrictions,
        )

        response = await call_ml(
            ml_client.add_user_product_compatibilities_batch,
            access_token=access_token,
            user_product_id=str(user_product_id),
            category_id=str(category_id),
            product_ids=product_ids,
            restrictions=restrictions if restrictions is not None else [],
            user_id=user_id,
            metrics=metrics,
            limiter=WRITE_RATE_LIMITER,
        )

        logger.info(
            "[BATCH][OK] item_id=%s user_product_id=%s products_sent=%s",
            item_id,
            user_product_id,
            len(product_ids),
        )

        created_count = 0
        if isinstance(response, dict):
            created_count = response.get("created_compatibilities_count", 0) or 0

        return {
            "ok": True,
            "item_id": item_id,
            "user_product_id": str(user_product_id),
            "category_id": str(category_id),
            "products_sent_count": len(product_ids),
            "product_ids": product_ids,
            "response": response,
            "created_compatibilities_count": created_count,
        }
    except HTTPException as exc:
        status_code = getattr(exc, "status_code", None)
        error_message = _extract_http_error_message(getattr(exc, "detail", exc))
        logger.warning(
            "[BATCH][HTTP_ERROR] item_id=%s status=%s products_sent=%s detail=%s",
            item_id,
            status_code,
            len(product_ids),
            error_message,
        )
        return {
            "ok": False,
            "item_id": item_id,
            "product_ids": product_ids,
            "status_code": status_code,
            "error_type": _error_type_for_status(status_code),
            "error_code": f"ML_HTTP_{status_code}" if status_code else "ML_HTTP_ERROR",
            "error_message": error_message,
            "response": None,
        }
    except Exception as exc:
        logger.exception(
            "[BATCH][UNEXPECTED_ERROR] item_id=%s products_sent=%s",
            item_id,
            len(product_ids),
        )
        return {
            "ok": False,
            "item_id": item_id,
            "product_ids": product_ids,
            "error_type": "technical",
            "error_code": "UNEXPECTED_BATCH_ERROR",
            "error_message": str(exc),
            "response": None,
        }


def build_final_row_results(
    resolved_rows: list[dict],
    batch_results: list[dict],
) -> list[dict]:
    ok_pairs: set[tuple[str, str]] = set()
    error_by_pair: dict[tuple[str, str], dict] = {}

    for batch in batch_results:
        item_id = str(batch.get("item_id") or "")
        product_ids = [str(pid) for pid in batch.get("product_ids", [])]

        if batch.get("ok"):
            for pid in product_ids:
                ok_pairs.add((item_id, pid))
        else:
            for pid in product_ids:
                error_by_pair[(item_id, pid)] = {
                    "error_type": batch.get("error_type", "technical"),
                    "error_code": batch.get("error_code", "BATCH_ERROR"),
                    "error_message": batch.get("error_message", "Error en batch"),
                }

    final_rows: list[dict] = []

    for row in resolved_rows:
        item_id = str(row.get("item_id") or "")
        product_id = str(row.get("product_id") or "")

        if not row.get("ok") or not product_id:
            final_rows.append(
                {
                    **row,
                    "ok": False,
                    "success_count": 0,
                    "error_count": 1,
                    "results": [
                        {
                            "ok": False,
                            "year": row.get("year"),
                            "reason": row.get("reason", row.get("error_message", "No se pudo resolver product_id")),
                            "error_type": row.get("error_type", "functional"),
                            "error_code": row.get("error_code", "PRODUCT_RESOLUTION_ERROR"),
                        }
                    ],
                }
            )
            continue

        pair = (item_id, product_id)

        if pair in ok_pairs:
            final_rows.append(
                {
                    **row,
                    "ok": True,
                    "success_count": 1,
                    "error_count": 0,
                    "year_requested": row.get("year"),
                    "year_processed": row.get("year"),
                    "results": [
                        {
                            "ok": True,
                            "year": row.get("year"),
                            "product_id": product_id,
                        }
                    ],
                }
            )
        else:
            error_info = error_by_pair.get(
                pair,
                {
                    "error_code": "MISSING_BATCH_CONFIRMATION",
                    "error_message": "No se encontró confirmación del batch para esta fila",
                },
            )
            final_rows.append(
                {
                    **row,
                    "ok": False,
                    "success_count": 0,
                    "error_count": 1,
                    "error_type": error_info.get("error_type", "technical"),
                    "year_requested": row.get("year"),
                    "results": [
                        {
                            "ok": False,
                            "year": row.get("year"),
                            "reason": error_info["error_message"],
                            "error_type": error_info.get("error_type", "technical"),
                            "error_code": error_info["error_code"],
                            "product_id": product_id,
                        }
                    ],
                }
            )

    return final_rows


def dedupe_final_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for row in rows:
        key = (
            str(row.get("item_id") or ""),
            str(row.get("product_id") or ""),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def build_compat_summary(final_rows: list[dict], batch_results: list[dict], metrics: JobMetrics) -> dict:
    deduped_rows = dedupe_final_rows(final_rows)

    processed_rows = len(final_rows)
    unique_compatibilities = len(deduped_rows)

    rows_ok = sum(1 for r in final_rows if r.get("ok"))
    rows_error = processed_rows - rows_ok

    unique_ok = sum(1 for r in deduped_rows if r.get("ok"))
    unique_error = unique_compatibilities - unique_ok

    functional_errors = sum(1 for r in final_rows if r.get("error_type") == "functional")
    technical_errors = sum(1 for r in final_rows if r.get("error_type") == "technical")

    return {
        "processed_rows": processed_rows,
        "total_rows": processed_rows,
        "excel_rows_processed": processed_rows,
        "success_count": rows_ok,
        "error_count": rows_error,
        "unique_compatibilities": unique_compatibilities,
        "unique_compatibilities_ok": unique_ok,
        "unique_compatibilities_error": unique_error,
        "compatibilities_total": processed_rows,
        "compatibilities_ok": rows_ok,
        "compatibilities_error": rows_error,
        "functional_errors": functional_errors,
        "technical_errors": technical_errors,
        "brands": len(
            {
                _norm(r.get("brand_name"))
                for r in final_rows
                if _safe_text(r.get("brand_name"))
            }
        ),
        "models": len(
            {
                f"{_norm(r.get('brand_name'))}::{_norm(r.get('model_name'))}"
                for r in final_rows
                if _safe_text(r.get("model_name"))
            }
        ),
        "items_count": len({str(r.get("item_id") or "") for r in final_rows if r.get("item_id")}),
        "batches_count": len(batch_results),
        "metrics": metrics.to_dict(),
    }

async def process_compatibility_batches(
    *,
    access_token: str,
    user_id: int | str,
    rows: list[dict],
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict:
    metrics = JobMetrics()
    grouped, restriction_data = build_grouped_product_ids(rows)
    batch_size = min(200, max(1, int(getattr(settings, "compat_batch_size", 200))))
    max_concurrency = max(1, int(getattr(settings, "compat_batch_concurrency", 4)))

    all_batches: list[tuple[str, list[str], list]] = []
    total_products = 0

    for item_id, product_ids in grouped.items():
        total_products += len(product_ids)
        rd = restriction_data.get(item_id, {})
        restrictions = build_restrictions(
            rd.get("familia", ""),
            rd.get("posicion_dt", ""),
            rd.get("posicion_id", ""),
        )
        #print("=" * 60)
        #print(f"[BATCH][BUILD_RESTRICTION] item_id={item_id} familia={rd.get('familia', '')} posicion_dt={rd.get('posicion_dt', '')} posicion_id={rd.get('posicion_id', '')}")
        #print(f"[BATCH][BUILD_RESTRICTION] restrictions={json.dumps(restrictions, indent=2, ensure_ascii=False)}")
        #print("=" * 60)
        #logger.info(
            #"[BATCH][BUILD_RESTRICTION] item_id=%s familia=%s posicion_dt=%s posicion_id=%s restrictions=%s",
            #item_id, rd.get('familia', ''), rd.get('posicion_dt', ''), rd.get('posicion_id', ''),
            #json.dumps(restrictions, ensure_ascii=False),
        #)
        
        for batch in chunked(product_ids, batch_size):
            all_batches.append((item_id, batch, restrictions))

    logger.info(
        "[BATCH][START] items_grouped=%s total_products=%s total_batches=%s batch_size=%s concurrency=%s",
        len(grouped),
        total_products,
        len(all_batches),
        batch_size,
        max_concurrency,
    )

    semaphore = asyncio.Semaphore(max_concurrency)
    progress_lock = asyncio.Lock()
    completed = 0
    batch_results: list[dict | None] = [None] * len(all_batches)

    async def worker(pos: int, item_id: str, batch: list[str], restrictions: list) -> None:
        nonlocal completed

        async with semaphore:
            try:
                result = await post_compatibilities_batch(
                    access_token=access_token,
                    user_id=user_id,
                    item_id=item_id,
                    product_ids=batch,
                    restrictions=restrictions,
                    metrics=metrics,
                )
            except Exception as exc:
                logger.exception(
                    "[BATCH][WORKER_ERROR] item_id=%s products_sent=%s",
                    item_id,
                    len(batch),
                )
                result = {
                    "ok": False,
                    "item_id": item_id,
                    "product_ids": batch,
                    "error_type": "technical",
                    "error_code": "WORKER_UNHANDLED_ERROR",
                    "error_message": str(exc),
                    "response": None,
                }
            batch_results[pos] = result

            should_notify = False
            completed_snapshot = 0

            async with progress_lock:
                completed += 1
                completed_snapshot = completed
                should_notify = on_progress is not None

            logger.info(
                "[BATCH][PROGRESS] completed=%s/%s last_item=%s sent=%s ok=%s",
                completed_snapshot,
                len(all_batches),
                item_id,
                len(batch),
                result.get("ok"),
            )

            if should_notify and on_progress is not None:
                try:
                    await on_progress(completed_snapshot, len(all_batches))
                except Exception:
                    logger.exception("[BATCH][WARN] fallo actualizando progreso")

    await asyncio.gather(
        *(worker(i, item_id, batch, restrictions) for i, (item_id, batch, restrictions) in enumerate(all_batches))
    )

    final_batch_results = [
        r if r is not None else {
            "ok": False,
            "error_code": "MISSING_BATCH_RESULT",
            "error_message": "Resultado faltante del batch",
            "item_id": "",
            "product_ids": [],
        }
        for r in batch_results
    ]

    final_rows = build_final_row_results(rows, final_batch_results)
    summary = build_compat_summary(final_rows, final_batch_results, metrics)

    total_created = sum(
        (r.get("created_compatibilities_count", 0) or 0)
        for r in final_batch_results
        if r.get("ok")
    )
    summary["total_created_compatibilities"] = total_created

    logger.info(
        "[BATCH][END] excel_rows=%s unique_compatibilities=%s ok=%s error=%s",
        summary["processed_rows"],
        summary["unique_compatibilities"],
        summary["compatibilities_ok"],
        summary["compatibilities_error"],
    )
    logger.info(
        "========================================\n"
        "  TOTAL COMPATIBILIDADES CREADAS: %s\n"
        "========================================",
        total_created,
    )

    return {
        "results": final_rows,
        "batch_results": final_batch_results,
        "summary": summary,
    }
