from math import ceil
from typing import Iterable, Sequence, TypeVar

from config import settings

T = TypeVar("T")


def get_process_file_chunk_size() -> int:
    return max(1, int(getattr(settings, "process_file_chunk_size", 100)))


def get_process_file_chunk_pause_seconds() -> int:
    return max(0, int(getattr(settings, "process_file_chunk_pause_seconds", 2 * 60)))


def get_compatibility_chunk_pause_seconds() -> int:
    return max(
        0,
        int(
            getattr(
                settings,
                "compatibility_chunk_pause_seconds",
                3 * 60 + 30,
            )
        ),
    )


def get_compatibility_exception_chunk_pause_seconds() -> int:
    return max(
        0,
        int(
            getattr(
                settings,
                "compatibility_exception_chunk_pause_seconds",
                get_process_file_chunk_pause_seconds(),
            )
        ),
    )


def get_price_stock_chunk_pause_seconds() -> int:
    return max(
        0,
        int(
            getattr(
                settings,
                "price_stock_chunk_pause_seconds",
                get_process_file_chunk_pause_seconds(),
            )
        ),
    )


def get_item_pictures_chunk_pause_seconds() -> int:
    return max(
        0,
        int(
            getattr(
                settings,
                "item_pictures_chunk_pause_seconds",
                get_process_file_chunk_pause_seconds(),
            )
        ),
    )


def get_sku_description_chunk_pause_seconds() -> int:
    return max(
        0,
        int(
            getattr(
                settings,
                "sku_description_chunk_pause_seconds",
                get_process_file_chunk_pause_seconds(),
            )
        ),
    )


def chunk_sequence(
    items: Sequence[T],
    chunk_size: int | None = None,
) -> Iterable[tuple[int, list[T]]]:
    size = max(1, int(chunk_size or get_process_file_chunk_size()))
    for start in range(0, len(items), size):
        yield start, list(items[start:start + size])


def count_chunks(total_items: int, chunk_size: int | None = None) -> int:
    if total_items <= 0:
        return 0
    size = max(1, int(chunk_size or get_process_file_chunk_size()))
    return ceil(total_items / size)


def format_pause_minutes(seconds: int) -> str:
    minutes = max(0, seconds) / 60
    if minutes.is_integer():
        return f"{int(minutes)} minutos"
    return f"{minutes:.1f} minutos"
