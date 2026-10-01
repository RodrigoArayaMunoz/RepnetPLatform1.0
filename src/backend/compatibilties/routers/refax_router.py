from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from services.refax_export_stream import stream_refax_export

from services.refax_connection_service import (
    RefaxConnectionError,
    refax_connection_service,
)
from services.refax_products_service import (
    RefaxProductsError,
    refax_products_service,
)

router = APIRouter(prefix="/refax", tags=["REFAX"])


@router.get("/status")
async def refax_status():
    try:
        return await refax_connection_service.status()
    except RefaxConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


@router.post("/connect")
async def refax_connect():
    try:
        return await refax_connection_service.connect()
    except RefaxConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get("/products/download")
async def download_refax_products(progress: bool = Query(False)):
    if progress is True:
        return StreamingResponse(
            stream_refax_export(refax_products_service),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )
    try:
        download = await refax_products_service.download()
    except RefaxProductsError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return Response(
        content=download.content,
        media_type=download.content_type.split(";", 1)[0],
        headers={
            "Content-Disposition": (
                f'attachment; filename="productos_refax_{timestamp}.xlsx"'
            ),
            "Cache-Control": "no-store",
        },
    )
