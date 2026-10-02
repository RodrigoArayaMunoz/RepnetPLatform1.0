"""Verify REFAX authentication and persistence without printing credentials."""

import argparse
import asyncio
import json
import sys

from services.refax_connection_service import (
    RefaxConnectionError,
    refax_connection_service,
)
from services.refax_products_service import RefaxProductsError, refax_products_service


async def check_connection(*, download_products: bool = False) -> dict:
    state = await refax_connection_service.connect()
    if not state["connected"]:
        raise RefaxConnectionError("REFAX no confirmo la conexion")
    result = {"connected": True, "expires_at": state["expires_at"]}
    if download_products:
        download = await refax_products_service.download()
        result["products_download_bytes"] = len(download.content)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-products", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(check_connection(download_products=args.download_products))
    except (RefaxConnectionError, RefaxProductsError) as error:
        print(f"[REFAX] {error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
