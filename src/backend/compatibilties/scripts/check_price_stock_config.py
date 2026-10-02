"""Check the effective write policy without sending requests to Mercado Libre."""

import json

from config import settings
from services.compatibility_service import (
    PRICE_STOCK_WRITE_RATE_LIMITER,
    WRITE_RATE_LIMITER,
)
from services.process_chunking_service import (
    get_price_stock_chunk_pause_seconds,
    get_price_stock_chunk_size,
)
from services.item_description_service import (
    ITEM_DESCRIPTION_RATE_LIMITER,
    ITEM_DESCRIPTION_WRITE_RATE_LIMITER,
)


def checked_profile() -> dict:
    limiter = PRICE_STOCK_WRITE_RATE_LIMITER
    if limiter is not WRITE_RATE_LIMITER:
        raise RuntimeError("Precios/stock debe usar el presupuesto global de escritura")
    expected = (
        settings.ml_write_requests_per_second,
        settings.ml_write_max_requests_per_window,
        settings.ml_write_window_seconds,
    )
    actual = (
        limiter.requests_per_second,
        limiter.max_requests_per_window,
        limiter.window_seconds,
    )
    if actual != expected:
        raise RuntimeError("El limitador no coincide con la configuracion cargada")
    description = ITEM_DESCRIPTION_RATE_LIMITER
    if ITEM_DESCRIPTION_WRITE_RATE_LIMITER.limiters != (description, WRITE_RATE_LIMITER):
        raise RuntimeError("Descripciones debe usar su presupuesto y el global")
    if (description.requests_per_second, description.max_requests_per_window,
        description.window_seconds) != (
        settings.ml_item_description_requests_per_second,
        settings.ml_item_description_max_requests_per_window,
        settings.ml_item_description_window_seconds,
    ):
        raise RuntimeError("El limitador de descripciones no coincide con la configuracion")
    return {
        "write_requests_per_second": actual[0],
        "write_max_requests_per_window": actual[1],
        "write_window_seconds": actual[2],
        "retry_429_cooldown_seconds": settings.ml_retry_429_cooldown_seconds,
        "price_stock_chunk_size": get_price_stock_chunk_size(),
        "price_stock_chunk_pause_seconds": get_price_stock_chunk_pause_seconds(),
        "price_stock_max_concurrency": settings.price_stock_max_concurrency,
        "job_progress_update_every": settings.job_progress_update_every,
        "process_queue_delay_seconds": settings.process_queue_delay_seconds,
        "write_namespace": limiter.namespace,
        "description_requests_per_second": description.requests_per_second,
        "description_max_requests_per_window": description.max_requests_per_window,
        "description_window_seconds": description.window_seconds,
        "description_chunk_size": settings.item_description_chunk_size,
        "description_max_concurrency": settings.item_description_max_concurrency,
        "description_namespace": description.namespace,
    }


def main() -> None:
    print(json.dumps(checked_profile(), sort_keys=True))


if __name__ == "__main__":
    main()
