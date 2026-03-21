from collections import defaultdict
from typing import Iterable

from config import settings
from services.compatibility_service import JobMetrics, WRITE_RATE_LIMITER, call_ml
from services.ml_client import ml_client
from services.product_cache_service import ProductCacheService


def chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def get_item_compact_cached(
    *,
    access_token: str,
    item_id: str,
    metrics: JobMetrics,
) -> dict:
    cached = ProductCacheService.get_item_compact(item_id)
    if cached:
        return cached

    item_detail = await call_ml(
        ml_client.get_item_detail,
        access_token,
        item_id,
        metrics=metrics,
    )

    compact = {
        "item_id": item_id,
        "category_id": item_detail.get("category_id"),
        "user_product_id": item_detail.get("user_product_id"),
    }
    ProductCacheService.set_item_compact(item_id, compact)
    return compact


def group_product_ids_by_item(rows: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        item_id = row.get("item_id")
        product_id = row.get("product_id")
        ok = row.get("ok")

        if not item_id or not product_id or not ok:
            continue

        grouped[str(item_id)].add(str(product_id))

    return {
        item_id: sorted(list(product_ids))
        for item_id, product_ids in grouped.items()
    }


async def post_compatibilities_batch(
    *,
    access_token: str,
    item_id: str,
    product_ids: list[str],
    metrics: JobMetrics,
) -> dict:
    print(f"[BATCH] Entré a post_compatibilities_batch item_id={item_id}", flush=True)
    item_compact = await get_item_compact_cached(
        access_token=access_token,
        item_id=item_id,
        metrics=metrics,
    )

    category_id = item_compact.get("category_id")
    user_product_id = item_compact.get("user_product_id")

    if not category_id or not user_product_id:
        print(
            f"[BATCH][ERROR] item_id={item_id} sin category_id o user_product_id",
            flush=True,
        )
        return {
            "ok": False,
            "item_id": item_id,
            "product_ids": product_ids,
            "error_code": "MISSING_ITEM_DATA",
            "error_message": "No se obtuvo category_id o user_product_id",
        }

    batch_size = min(200, max(1, int(getattr(settings, "compat_batch_size", 200))))
    product_ids = [str(pid) for pid in product_ids][:batch_size]

    print(
    f"[BATCH] item_id={item_id} user_product_id={user_product_id} "
    f"products_sent={len(product_ids)}",
    flush=True,
)
    
    response = await call_ml(
        ml_client.add_user_product_compatibilities_batch,
        access_token=access_token,
        user_product_id=str(user_product_id),
        category_id=str(category_id),
        product_ids=product_ids,
        creation_source="DEFAULT",
        metrics=metrics,
        limiter=WRITE_RATE_LIMITER,
    )

    return {
        "ok": True,
        "item_id": item_id,
        "user_product_id": str(user_product_id),
        "category_id": str(category_id),
        "products_sent_count": len(product_ids),
        "product_ids": product_ids,
        "response": response,
    }


async def process_compatibility_batches(
    *,
    access_token: str,
    rows: list[dict],
) -> dict:
    metrics = JobMetrics()
    grouped = group_product_ids_by_item(rows)
    batch_size = min(200, max(1, int(getattr(settings, "compat_batch_size", 200))))

    batch_results: list[dict] = []

    for item_id, product_ids in grouped.items():
        for batch in chunked(product_ids, batch_size):
            result = await post_compatibilities_batch(
                access_token=access_token,
                item_id=item_id,
                product_ids=batch,
                metrics=metrics,
            )
            batch_results.append(result)

    ok_batches = sum(1 for r in batch_results if r.get("ok"))
    error_batches = len(batch_results) - ok_batches

    return {
        "results": batch_results,
        "summary": {
            "items_count": len(grouped),
            "batches_count": len(batch_results),
            "ok_batches": ok_batches,
            "error_batches": error_batches,
            "metrics": metrics.to_dict(),
        },
    }