"""Process whole listings; copy only a verified, exact, seller-created set."""
import asyncio
import hashlib
import json
import logging
from collections import Counter, defaultdict

from config import settings
from services.compatibility_batch_service import (
    _applied_row_key, build_compat_summary, build_final_row_results,
    build_grouped_product_families, process_compatibility_batches, validate_resolved_family_rows,
)
from services.compatibility_family_cache import get_family_resolution, save_family_resolution
from services.compatibility_service import COMPATIBILITY_WRITE_RATE_LIMITER, READ_RATE_LIMITER, call_ml
from services.ml_client import ml_client

logger = logging.getLogger(__name__)


def _positions(restrictions):
    return tuple(sorted(tuple(sorted(value["value_id"] for value in combination["values"]))
                        for restriction in (restrictions or [])
                        for combination in restriction.get("attribute_values", [])))


def _products(response):
    if not isinstance(response, dict) or not isinstance(response.get("products"), list):
        return None
    products = response["products"]
    if any(not isinstance(product, dict) or not product.get("catalog_product_id") or product.get("source") != "SELLER" for product in products):
        return None
    paging = response.get("paging", response.get("pagination", {})) or {}
    if (int(paging.get("total", response.get("total", len(products)))) > len(products)
            or response.get("catalog_compatibilities_count", 0)):
        return None
    if len({product["catalog_product_id"] for product in products}) != len(products):
        return None
    return products


def _snapshot(products):
    return {product["catalog_product_id"]: {"note": product.get("note", ""), "positions": _positions(product.get("restrictions"))}
            for product in products}


def _equal_snapshot(products, snapshot, *, exact=False):
    actual = json.loads(json.dumps(_snapshot(products)))
    snapshot = json.loads(json.dumps(snapshot))
    return (not exact or set(actual) == set(snapshot)) and all(actual.get(key) == value for key, value in snapshot.items())


def _signature(entries, user_id):
    payloads = []
    for entry in entries:
        payload = entry["payload"]
        payloads.append({"domain_id": payload["domain_id"],
                         "attributes": sorted(payload["attributes"], key=lambda attr: attr["id"]),
                         "note": payload.get("note", ""), "positions": _positions(payload.get("restrictions"))})
    value = {"version": 1, "user_id": str(user_id), "category_id": entries[0]["item_compact"]["category_id"],
             "families": sorted(payloads, key=lambda payload: json.dumps(payload, sort_keys=True))}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def process_complete_item_batches(*, access_token, user_id, rows, metrics, state,
                                        copy_candidates: set[str] | frozenset[str] = frozenset(), on_progress=None):
    by_item = defaultdict(list)
    for row in validate_resolved_family_rows(rows):
        by_item[str(row.get("item_id") or "")].append(row)
    prepared = {}
    for item_id, item_rows in by_item.items():
        entries = build_grouped_product_families(item_rows).get(item_id, [])
        signature = _signature(entries, user_id) if entries and all(row.get("ok") for row in item_rows) else None
        prepared[item_id] = (item_rows, entries, signature)
    groups = defaultdict(list)
    for item_id, (_, _, signature) in prepared.items():
        groups[signature or ("invalid:" + item_id)].append(item_id)
    outcomes = {}
    semaphore = asyncio.Semaphore(settings.compat_batch_concurrency)
    completed = 0
    created = 0
    progress_lock = asyncio.Lock()

    async def read(up):
        response = await call_ml(
            ml_client.get_user_product_compatibilities, access_token=access_token,
            user_product_id=up, user_id=user_id, metrics=metrics, limiter=READ_RATE_LIMITER,
            client_managed_retry=True,
        )
        return _products(response)

    def successful_reuse(item_rows, entries, created_count, mode, response):
        batch = {"ok": True, "item_id": item_rows[0]["item_id"], "compatibility_mode": "product_family",
                 "compatibility_keys": [entry["compatibility_key"] for entry in entries],
                 "created_compatibilities_count": created_count, "families_sent_count": len(entries),
                 "matched_products_count": sum(entry["matched_products_count"] for entry in entries),
                 "write_strategy": mode, "response": response}
        final_rows = build_final_row_results(item_rows, [batch])
        for row in final_rows:
            state.successful_rows[_applied_row_key(row)] = row
        for entry in entries:
            if entry.get("uses_position_columns"):
                state.position_restrictions[(str(batch["item_id"]), entry["compatibility_key"])] = entry["payload"].get("restrictions", [])
        return {"results": final_rows, "batch_results": [batch], "summary": build_compat_summary(final_rows, [batch], metrics)}

    async def item_worker(item_id):
        item_rows, entries, signature = prepared[item_id]
        if not signature:
            return await process_compatibility_batches(access_token=access_token, user_id=user_id, rows=item_rows, metrics=metrics, state=state)
        target = entries[0]["item_compact"]
        up = target["user_product_id"]
        checkpoint_key = "completed:" + str(user_id) + ":" + up + ":" + signature
        copy_key = "copy:" + signature
        before = None
        source = None
        copy_created_count = 0
        # Optional optimization failures always fall back to ordinary family writes.
        try:
            checkpoint = await get_family_resolution(checkpoint_key)
            if checkpoint and checkpoint.get("products"):
                before = await read(up)
                if before is not None and _equal_snapshot(before, checkpoint["products"]):
                    return successful_reuse(item_rows, entries, 0, "already_applied", {})
            if settings.compatibility_enable_copy and item_id in copy_candidates:
                source = state.copy_sources.get(signature) or await get_family_resolution(copy_key)
                if before is None:
                    before = await read(up)
                if source and before == [] and source["user_product_id"] != up:
                    source_products = await read(source["user_product_id"])
                    if (source_products is not None and not any(product.get("reputation") for product in source_products)
                            and _equal_snapshot(source_products, source["products"], exact=True)):
                        await call_ml(
                            ml_client.copy_user_product_compatibilities, access_token=access_token,
                            user_product_id=up, source_user_product_id=source["user_product_id"],
                            category_id=target["category_id"], user_id=user_id, metrics=metrics,
                            limiter=COMPATIBILITY_WRITE_RATE_LIMITER, client_managed_retry=True,
                        )
                        copied = await read(up)
                        if copied is not None and _equal_snapshot(copied, source["products"], exact=True):
                            await save_family_resolution(checkpoint_key, {"products": _snapshot(copied)})
                            return successful_reuse(item_rows, entries, len(copied), "copy", {})
                        if copied is not None and set(_snapshot(copied)).issubset(source["products"]):
                            copy_created_count = len(copied)
                        logger.warning("[COMPATIBILITY][COPY_INCOMPLETE] item=%s using family writes", item_id)
        except Exception:
            logger.warning("[COMPATIBILITY][COPY_OR_CHECKPOINT_UNAVAILABLE] item=%s using family writes", item_id)
        outcome = await process_compatibility_batches(access_token=access_token, user_id=user_id, rows=item_rows, metrics=metrics, state=state)
        if copy_created_count and outcome["batch_results"]:
            first_batch = outcome["batch_results"][0]
            first_batch["copy_created_compatibilities_count"] = copy_created_count
            first_batch["created_compatibilities_count"] = first_batch.get("created_compatibilities_count", 0) + copy_created_count
            first_batch["creation_committed"] = True
            outcome["summary"] = build_compat_summary(outcome["results"], outcome["batch_results"], metrics)
        if all(row.get("ok") for row in outcome["results"]) and (settings.compatibility_family_cache_ttl_seconds or (settings.compatibility_enable_copy and item_id in copy_candidates)):
            try:
                after = await read(up)
                if after is not None:
                    expected = Counter()
                    for entry in entries:
                        expected[(entry["payload"].get("note", ""), _positions(entry["payload"].get("restrictions")))] += entry["matched_products_count"]
                    actual = Counter((product.get("note", ""), _positions(product.get("restrictions"))) for product in after)
                    if expected == actual and after:
                        await save_family_resolution(checkpoint_key, {"products": _snapshot(after)})
                    if (settings.compatibility_enable_copy and item_id in copy_candidates and before == [] and expected == actual
                            and not any(product.get("reputation") for product in after) and 0 < len(after) <= 200):
                        source = {"user_product_id": up, "products": _snapshot(after)}
                        state.copy_sources[signature] = source
                        await save_family_resolution(copy_key, source)
            except Exception:
                logger.warning("[COMPATIBILITY][CHECKPOINT_UNAVAILABLE] item=%s", item_id)
        return outcome

    async def group_worker(item_ids):
        nonlocal completed, created
        for item_id in item_ids:
            async with semaphore:
                outcome = await item_worker(item_id)
                outcomes[item_id] = outcome
            async with progress_lock:
                completed += 1
                created += outcome["summary"].get("total_created_compatibilities", 0)
                if on_progress:
                    await on_progress(completed, len(by_item), created)

    await asyncio.gather(*(group_worker(item_ids) for item_ids in groups.values()))
    final_rows = [row for item_id in by_item for row in outcomes[item_id]["results"]]
    final_batches = [batch for item_id in by_item for batch in outcomes[item_id]["batch_results"]]
    return {"results": final_rows, "batch_results": final_batches,
            "summary": build_compat_summary(final_rows, final_batches, metrics)}
