"""Check the effective write policy without sending requests to Mercado Libre."""

import json

from config import settings
from services.compatibility_service import (
    PRICE_STOCK_WRITE_RATE_LIMITER,
    WRITE_RATE_LIMITER,
    READ_RATE_LIMITER,
    ITEM_PICTURES_RATE_LIMITER,
    ITEM_PICTURES_WRITE_RATE_LIMITER,
    COMPATIBILITY_RATE_LIMITER,
    COMPATIBILITY_WRITE_RATE_LIMITER,
    COMPATIBILITY_EXCEPTION_WRITE_RATE_LIMITER,
)
from services.process_chunking_service import (
    get_price_stock_chunk_pause_seconds,
    get_price_stock_chunk_size,
    get_item_pictures_chunk_size,
    get_item_pictures_chunk_pause_seconds,
    get_compatibility_chunk_size,
    get_compatibility_chunk_pause_seconds,
    get_compatibility_exception_chunk_pause_seconds,
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
    if READ_RATE_LIMITER.requests_per_second != settings.ml_read_requests_per_second:
        raise RuntimeError("El limitador de lectura no coincide con la configuracion")
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
    endpoint_profiles = {}
    for name, endpoint, writer, expected in (
        ("pictures", ITEM_PICTURES_RATE_LIMITER, ITEM_PICTURES_WRITE_RATE_LIMITER,
         (settings.ml_item_pictures_requests_per_second,
          settings.ml_item_pictures_max_requests_per_window, settings.ml_item_pictures_window_seconds)),
        ("compatibility", COMPATIBILITY_RATE_LIMITER, COMPATIBILITY_WRITE_RATE_LIMITER,
         (settings.ml_compatibility_write_requests_per_second,
          settings.ml_compatibility_max_requests_per_window, settings.ml_compatibility_window_seconds)),
    ):
        if writer.limiters != (endpoint, WRITE_RATE_LIMITER):
            raise RuntimeError(f"{name} debe usar su presupuesto y el global")
        actual_endpoint = (endpoint.requests_per_second, endpoint.max_requests_per_window,
                           endpoint.window_seconds)
        if actual_endpoint != expected:
            raise RuntimeError(f"El limitador de {name} no coincide con la configuracion")
        if endpoint.client.connection_pool.connection_kwargs != limiter.client.connection_pool.connection_kwargs:
            raise RuntimeError(f"{name} debe compartir Redis con el presupuesto global")
        endpoint_profiles.update({
            f"{name}_requests_per_second": actual_endpoint[0],
            f"{name}_max_requests_per_window": actual_endpoint[1],
            f"{name}_window_seconds": actual_endpoint[2],
            f"{name}_namespace": endpoint.namespace,
        })
    if COMPATIBILITY_EXCEPTION_WRITE_RATE_LIMITER is not COMPATIBILITY_WRITE_RATE_LIMITER:
        raise RuntimeError("Excepciones debe compartir el presupuesto de compatibilidades")
    return {
        **endpoint_profiles,
        "read_requests_per_second": READ_RATE_LIMITER.requests_per_second,
        "http_max_connections": settings.ml_http_max_connections,
        "http_max_keepalive": settings.ml_http_max_keepalive,
        "retry_attempts": settings.ml_retry_attempts,
        "pictures_chunk_size": get_item_pictures_chunk_size(),
        "pictures_chunk_pause_seconds": get_item_pictures_chunk_pause_seconds(),
        "pictures_max_concurrency": settings.item_pictures_max_concurrency,
        "pictures_http_timeout_seconds": settings.ml_item_pictures_http_timeout_seconds,
        "compatibility_chunk_size": get_compatibility_chunk_size(),
        "compatibility_chunk_pause_seconds": get_compatibility_chunk_pause_seconds(),
        "compatibility_exception_chunk_pause_seconds": get_compatibility_exception_chunk_pause_seconds(),
        "compatibility_max_concurrency": settings.compatibility_max_concurrency,
        "compatibility_batch_concurrency": settings.compat_batch_concurrency,
        "compatibility_http_timeout_seconds": settings.ml_compatibility_http_timeout_seconds,
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
