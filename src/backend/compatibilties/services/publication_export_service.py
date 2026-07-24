import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill

from config import settings
from services.job_store import JobStore
from services.ml_client import ml_client
from services.publication_description_cache import (
    PublicationDescriptionCache,
)
from services.publication_export_checkpoint import (
    PublicationExportCheckpoint,
)
from services.redis_rate_limiter import RedisWindowRateLimiter
from services.supabase_publications_store import supabase_publications_store

logger = logging.getLogger(__name__)

_ILLEGAL_EXCEL_CHARACTERS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F]"
)
_MAX_EXCEL_CELL_LENGTH = 32_767
_EXPORT_ARTIFACT_NAME = re.compile(
    r"^[0-9a-fA-F-]{36}_publicaciones_\d{4}-\d{2}-\d{2}"
    r"(?:\.xlsx(?:\.tmp)?|\.checkpoint\.jsonl)$"
)


def _excel_text(value: Any) -> str:
    text = _ILLEGAL_EXCEL_CHARACTERS.sub("", str(value or ""))
    return text[:_MAX_EXCEL_CELL_LENGTH]


class PublicationExportService:
    async def export(
        self,
        *,
        job_id: str,
        user_id: str,
        creation_date: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        self._cleanup_expired_artifacts()
        publications = await supabase_publications_store.list_by_creation_date(
            creation_date
        )
        total = len(publications)
        if total == 0:
            raise ValueError(
                f"No existen publicaciones con fecha de creacion {creation_date}."
            )

        source_fingerprint = self._source_fingerprint(publications)
        checkpoint = PublicationExportCheckpoint(
            path=self._checkpoint_path(
                job_id=job_id,
                creation_date=creation_date,
            ),
            source_fingerprint=source_fingerprint,
            total_rows=total,
        )
        checkpoint_state = checkpoint.load_or_initialize(publications)

        if heartbeat is not None:
            heartbeat()

        resumed = checkpoint_state.processed_rows > 0
        JobStore.update(
            job_id,
            status="processing",
            progress=self._progress(
                checkpoint_state.processed_rows,
                total,
            ),
            total_rows=total,
            processed_rows=checkpoint_state.processed_rows,
            descriptions_found=checkpoint_state.descriptions_found,
            descriptions_missing=checkpoint_state.descriptions_missing,
            descriptions_failed=checkpoint_state.descriptions_failed,
            cache_hits=checkpoint_state.cache_hits,
            api_items_queried=checkpoint_state.api_items_queried,
            source_fingerprint=source_fingerprint,
            checkpoint_path=str(checkpoint.path),
            last_error=None,
            message=(
                "Reanudando exportacion desde "
                f"{checkpoint_state.processed_rows}/{total} publicaciones..."
                if resumed
                else (
                    f"Consultando descripciones de {total} publicaciones "
                    "en Mercado Libre..."
                )
            ),
        )

        limiter = RedisWindowRateLimiter(
            redis_url=settings.redis_url,
            namespace="ml:read",
            requests_per_second=float(
                settings.ml_publication_export_requests_per_second
            ),
        )
        cache = PublicationDescriptionCache(settings.redis_url)
        semaphore = asyncio.Semaphore(
            int(settings.ml_publication_export_concurrency)
        )
        batch_size = int(settings.ml_publication_export_batch_size)

        try:
            for start in range(
                checkpoint_state.processed_rows,
                total,
                batch_size,
            ):
                if heartbeat is not None:
                    heartbeat()

                publication_batch = publications[
                    start : min(start + batch_size, total)
                ]
                rows = await self._resolve_batch(
                    publications=publication_batch,
                    user_id=user_id,
                    limiter=limiter,
                    cache=cache,
                    semaphore=semaphore,
                )

                if heartbeat is not None:
                    heartbeat()
                checkpoint.append_rows(rows)
                for row in rows:
                    checkpoint_state.add(row)

                if heartbeat is not None:
                    heartbeat()

                JobStore.update(
                    job_id,
                    status="processing",
                    progress=self._progress(
                        checkpoint_state.processed_rows,
                        total,
                    ),
                    processed_rows=checkpoint_state.processed_rows,
                    descriptions_found=(
                        checkpoint_state.descriptions_found
                    ),
                    descriptions_missing=(
                        checkpoint_state.descriptions_missing
                    ),
                    descriptions_failed=(
                        checkpoint_state.descriptions_failed
                    ),
                    cache_hits=checkpoint_state.cache_hits,
                    api_items_queried=checkpoint_state.api_items_queried,
                    message=(
                        "Descripciones procesadas: "
                        f"{checkpoint_state.processed_rows}/{total}; "
                        f"{checkpoint_state.cache_hits} recuperadas de cache."
                    ),
                )
        finally:
            await cache.close()
            await limiter.client.aclose()

        if checkpoint_state.processed_rows != total:
            raise RuntimeError(
                "El checkpoint de exportacion no contiene todas las "
                "publicaciones esperadas."
            )

        if heartbeat is not None:
            heartbeat()
        JobStore.update(
            job_id,
            status="processing",
            progress=97,
            message="Construyendo el archivo Excel...",
        )

        output_path = self._output_path(
            job_id=job_id,
            creation_date=creation_date,
        )
        self._build_excel_atomically(
            checkpoint=checkpoint,
            output_path=output_path,
            total=total,
        )

        filename = f"publicaciones_{creation_date}.xlsx"
        summary = {
            "total_rows": total,
            "descriptions_found": checkpoint_state.descriptions_found,
            "descriptions_missing": checkpoint_state.descriptions_missing,
            "descriptions_failed": checkpoint_state.descriptions_failed,
            "cache_hits": checkpoint_state.cache_hits,
            "api_items_queried": checkpoint_state.api_items_queried,
            "resumed_from_checkpoint": resumed,
        }
        JobStore.update(
            job_id,
            status="success",
            progress=100,
            processed_rows=total,
            result_path=output_path,
            output_filename=filename,
            summary=summary,
            last_error=None,
            message=(
                f"Excel listo: {total} publicaciones procesadas; "
                f"{checkpoint_state.cache_hits} recuperadas de cache."
            ),
        )

        try:
            checkpoint.delete()
        except OSError:
            logger.exception(
                "[PUBLICATION_EXPORT][CHECKPOINT_CLEANUP_ERROR] "
                "job_id=%s path=%s",
                job_id,
                checkpoint.path,
            )

        return summary

    async def _resolve_batch(
        self,
        *,
        publications: list[dict[str, Any]],
        user_id: str,
        limiter: RedisWindowRateLimiter,
        cache: PublicationDescriptionCache,
        semaphore: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            *(
                self._resolve_publication(
                    publication=publication,
                    user_id=user_id,
                    limiter=limiter,
                    cache=cache,
                    semaphore=semaphore,
                )
                for publication in publications
            ),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, BaseException):
                raise result

        return [
            result
            for result in results
            if isinstance(result, dict)
        ]

    async def _resolve_publication(
        self,
        *,
        publication: dict[str, Any],
        user_id: str,
        limiter: RedisWindowRateLimiter,
        cache: PublicationDescriptionCache,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        item_id = str(publication.get("mlc") or "")

        async with semaphore:
            cached = await cache.get(item_id)
            if cached is not None:
                description, result = cached
                source = "cache"
            else:
                description, result = await self._fetch_description(
                    item_id=item_id,
                    user_id=user_id,
                    limiter=limiter,
                )
                source = "api" if item_id else "invalid"
                if result in {"found", "missing"}:
                    await cache.set(
                        item_id,
                        description=description,
                        result=result,
                    )

        return {
            "mlc": _excel_text(publication.get("mlc")),
            "sku": _excel_text(publication.get("sku")),
            "titulo": _excel_text(publication.get("titulo")),
            "descripcion": _excel_text(description),
            "result": result,
            "source": source,
        }

    async def _fetch_description(
        self,
        *,
        item_id: str,
        user_id: str,
        limiter: RedisWindowRateLimiter,
    ) -> tuple[str, str]:
        if not item_id:
            return "", "failed"

        try:
            payload = await ml_client.request(
                "GET",
                f"/items/{quote(item_id, safe='')}/description",
                user_id=user_id,
                rate_limiter=limiter,
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                return "", "missing"
            if exc.status_code in {400, 410}:
                logger.warning(
                    "[PUBLICATION_EXPORT][DESCRIPTION_UNAVAILABLE] "
                    "item_id=%s status=%s",
                    item_id,
                    exc.status_code,
                )
                return "", "failed"
            raise

        if not isinstance(payload, dict):
            return "", "failed"

        description = str(
            payload.get("plain_text") or payload.get("text") or ""
        ).strip()
        if not description:
            return "", "missing"

        return description, "found"

    def _build_excel_atomically(
        self,
        *,
        checkpoint: PublicationExportCheckpoint,
        output_path: str,
        total: int,
    ) -> None:
        workbook, worksheet = self._create_workbook()
        written_rows = 0
        for row in checkpoint.iter_rows():
            worksheet.append(
                [
                    _excel_text(row.get("mlc")),
                    _excel_text(row.get("sku")),
                    _excel_text(row.get("titulo")),
                    _excel_text(row.get("descripcion")),
                ]
            )
            written_rows += 1

        if written_rows != total:
            raise RuntimeError(
                "No fue posible construir el Excel completo desde el "
                "checkpoint."
            )

        os.makedirs(settings.upload_dir, exist_ok=True)
        worksheet.auto_filter.ref = f"A1:D{total + 1}"
        temporary_output_path = f"{output_path}.tmp"
        try:
            workbook.save(temporary_output_path)
            os.replace(temporary_output_path, output_path)
        finally:
            try:
                os.remove(temporary_output_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _source_fingerprint(
        publications: list[dict[str, Any]],
    ) -> str:
        hasher = hashlib.sha256()
        for publication in publications:
            normalized = [
                str(publication.get("mlc") or ""),
                str(publication.get("sku") or ""),
                str(publication.get("titulo") or ""),
            ]
            hasher.update(
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            hasher.update(b"\n")
        return hasher.hexdigest()

    @staticmethod
    def _progress(processed_rows: int, total_rows: int) -> int:
        return min(
            95,
            2 + int((processed_rows / max(total_rows, 1)) * 93),
        )

    @staticmethod
    def _cleanup_expired_artifacts() -> None:
        uploads_root = Path(settings.upload_dir)
        if not uploads_root.exists():
            return

        cutoff = (
            time.time()
            - int(settings.ml_publication_export_artifact_ttl_seconds)
        )
        for path in uploads_root.iterdir():
            if (
                not path.is_file()
                or not _EXPORT_ARTIFACT_NAME.fullmatch(path.name)
            ):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                logger.exception(
                    "[PUBLICATION_EXPORT][ARTIFACT_CLEANUP_ERROR] "
                    "path=%s",
                    path,
                )

    @staticmethod
    def _create_workbook():
        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet("Publicaciones")
        worksheet.freeze_panes = "A2"
        worksheet.column_dimensions["A"].width = 20
        worksheet.column_dimensions["B"].width = 24
        worksheet.column_dimensions["C"].width = 55
        worksheet.column_dimensions["D"].width = 100

        header_fill = PatternFill("solid", fgColor="3483FA")
        header_font = Font(color="FFFFFF", bold=True)
        header_alignment = Alignment(
            horizontal="center",
            vertical="center",
        )
        header = []
        for value in ("MLC", "SKU", "TITULO", "DESCRIPCION"):
            cell = WriteOnlyCell(worksheet, value=value)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            header.append(cell)
        worksheet.append(header)

        return workbook, worksheet

    @staticmethod
    def _checkpoint_path(*, job_id: str, creation_date: str) -> str:
        return os.path.join(
            settings.upload_dir,
            f"{job_id}_publicaciones_{creation_date}.checkpoint.jsonl",
        )

    @staticmethod
    def _output_path(*, job_id: str, creation_date: str) -> str:
        return os.path.join(
            settings.upload_dir,
            f"{job_id}_publicaciones_{creation_date}.xlsx",
        )


publication_export_service = PublicationExportService()
