import asyncio
import base64
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from threading import Event

from services.refax_products_service import RefaxProductsDownload, RefaxProductsError

logger = logging.getLogger(__name__)


async def stream_refax_export(service):
    """Progreso y archivo de una sola consulta autenticada, sin trabajos huérfanos."""
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stopped = Event()
    last_event = None

    def enqueue(event):
        nonlocal last_event
        if event != last_event:
            last_event = event
            queue.put_nowait(event)

    def report(percentage, stage, message):
        if stopped.is_set():
            raise asyncio.CancelledError()
        loop.call_soon_threadsafe(enqueue, {
            "type": "progress", "percentage": percentage,
            "stage": stage, "message": message,
        })

    async def run():
        try:
            result = await service.download(on_progress=report)
            # Vaciar las notificaciones programadas por el hilo de Excel antes
            # de poner el archivo terminado en la cola.
            await asyncio.sleep(0)
            await queue.put(result)
        except RefaxProductsError as error:
            await queue.put({"type": "error", "message": str(error)})
        except Exception as error:
            logger.error("[REFAX_EXPORT_STREAM] error_type=%s", type(error).__name__)
            await queue.put({"type": "error", "message": "No se pudo generar el Excel. Vuelve a intentarlo."})

    def encode(event):
        return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    task = asyncio.create_task(run())
    try:
        yield encode({"type": "progress", "stage": "connecting", "percentage": 0,
                      "message": "Verificando la conexión con REFAX…"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
            except asyncio.TimeoutError:
                yield encode({"type": "heartbeat"})
                continue
            if isinstance(event, RefaxProductsDownload):
                timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
                yield encode({"type": "file", "filename": f"productos_refax_{timestamp}.xlsx",
                              "content_type": event.content_type, "size": len(event.content)})
                for offset in range(0, len(event.content), 32 * 1024):
                    yield encode({"type": "chunk", "data": base64.b64encode(
                        event.content[offset:offset + 32 * 1024]
                    ).decode("ascii")})
                yield encode({"type": "complete"})
                break
            yield encode(event)
            if event["type"] == "error":
                break
    finally:
        stopped.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
