import asyncio
import logging
import math
import re
import time
import uuid
from datetime import date
from typing import Any

from fastapi import HTTPException

from config import settings
from services.ml_client import ml_client
from services.publication_sync_store import publication_sync_store
from services.redis_rate_limiter import RedisWindowRateLimiter
from services.supabase_publications_store import supabase_publications_store

logger = logging.getLogger(__name__)


class PublicationSyncService:
    SCAN_LIMIT = 100
    MULTIGET_CHUNK_SIZE = 20
    MULTIGET_CONCURRENCY = 4
    MULTIGET_ATTRIBUTES = "id,title,attributes,date_created"
    MAX_SCAN_PAGES = 5000

    def __init__(self) -> None:
        self._read_rate_limiter: RedisWindowRateLimiter | None = None

    def start_request_context(self) -> None:
        self._read_rate_limiter = RedisWindowRateLimiter(
            redis_url=settings.redis_url,
            namespace="ml:read",
            requests_per_second=float(settings.ml_read_requests_per_second),
        )

    async def close_request_context(self) -> None:
        if self._read_rate_limiter is None:
            return

        await self._read_rate_limiter.client.aclose()
        self._read_rate_limiter = None

    async def sync_publications(self, *, user_id: str) -> dict[str, Any]:
        started_at = time.monotonic()
        sync_run_id = str(uuid.uuid4())
        await supabase_publications_store.ensure_ready()
        seller_id = await self._resolve_seller_id(user_id)

        publication_sync_store.update(
            status="scanning",
            seller_id=seller_id,
            sync_run_id=sync_run_id,
            message="Listando publicaciones en Mercado Libre...",
        )

        item_ids = await self._scan_all_item_ids(
            seller_id=seller_id,
            user_id=user_id,
        )
        total_items = len(item_ids)
        publication_sync_store.update(
            status="fetching",
            expected_count=total_items,
            scanned_count=total_items,
            progress=15,
            message=f"Consultando detalles de {total_items} publicaciones...",
        )

        saved_count = 0
        processed_count = 0
        failed_count = 0
        multiget_batches = 0
        rows_to_save: list[dict[str, Any]] = []
        detail_chunks = [
            item_ids[index : index + self.MULTIGET_CHUNK_SIZE]
            for index in range(0, total_items, self.MULTIGET_CHUNK_SIZE)
        ]

        for group_start in range(
            0,
            len(detail_chunks),
            self.MULTIGET_CONCURRENCY,
        ):
            chunk_group = detail_chunks[
                group_start : group_start + self.MULTIGET_CONCURRENCY
            ]
            responses = await asyncio.gather(
                *(
                    self._fetch_multiget_chunk(
                        chunk=chunk,
                        user_id=user_id,
                        seller_id=seller_id,
                        sync_run_id=sync_run_id,
                    )
                    for chunk in chunk_group
                ),
                return_exceptions=True,
            )

            group_errors: list[Exception] = []
            for chunk_response in responses:
                if isinstance(chunk_response, Exception):
                    group_errors.append(chunk_response)
                    continue

                parsed_rows, chunk_failures = chunk_response
                rows_to_save.extend(parsed_rows)
                processed_count += len(parsed_rows) + chunk_failures
                failed_count += chunk_failures
                multiget_batches += 1

            while len(rows_to_save) >= supabase_publications_store.UPSERT_BATCH_SIZE:
                batch = rows_to_save[: supabase_publications_store.UPSERT_BATCH_SIZE]
                saved_batch_count = (
                    await supabase_publications_store.upsert_rows(batch)
                )
                saved_count += saved_batch_count
                del rows_to_save[: len(batch)]

            if group_errors and rows_to_save:
                pending_rows = list(rows_to_save)
                saved_count += await supabase_publications_store.upsert_rows(
                    pending_rows
                )
                rows_to_save.clear()

            progress = 15 + int(
                (processed_count / max(total_items, 1)) * 80
            )
            publication_sync_store.update(
                status="saving",
                processed_count=processed_count,
                saved_count=saved_count,
                failed_count=failed_count,
                multiget_batches=multiget_batches,
                progress=min(progress, 95),
                message=(
                    f"Procesadas {processed_count}/{total_items} publicaciones; "
                    f"{saved_count} guardadas."
                ),
            )

            if group_errors:
                logger.error(
                    "[PUBLICATION_SYNC][MULTIGET_GROUP_ERROR] "
                    "seller_id=%s successful_chunks=%s failed_chunks=%s "
                    "processed=%s saved=%s",
                    seller_id,
                    len(chunk_group) - len(group_errors),
                    len(group_errors),
                    processed_count,
                    saved_count,
                )
                raise group_errors[0]

        if rows_to_save:
            saved_count += await supabase_publications_store.upsert_rows(rows_to_save)

        verified_count = await supabase_publications_store.count_by_sync_run(
            seller_id=seller_id,
            sync_run_id=sync_run_id,
        )
        run_is_complete = (
            failed_count == 0
            and processed_count == total_items
            and saved_count == total_items
            and verified_count == total_items
        )

        if run_is_complete:
            await supabase_publications_store.delete_stale_rows(
                seller_id=seller_id,
                sync_run_id=sync_run_id,
            )
            final_status = "success"
            final_message = (
                f"Carga completada: {saved_count} publicaciones guardadas en Supabase."
            )
        else:
            final_status = "partial"
            final_message = (
                f"Carga parcial: {verified_count}/{total_items} publicaciones "
                f"verificadas en Supabase y {failed_count} sin detalle. "
                "No se eliminaron registros anteriores."
            )

        summary = {
            "seller_id": seller_id,
            "expected_count": total_items,
            "scanned_count": total_items,
            "processed_count": processed_count,
            "saved_count": saved_count,
            "verified_count": verified_count,
            "failed_count": failed_count,
            "multiget_batches": multiget_batches,
            "duration_seconds": round(time.monotonic() - started_at, 2),
        }
        publication_sync_store.finish(
            status=final_status,
            message=final_message,
            **summary,
        )

        logger.info(
            "[PUBLICATION_SYNC][FINISHED] seller_id=%s status=%s summary=%s",
            seller_id,
            final_status,
            summary,
        )
        return summary

    async def _resolve_seller_id(self, user_id: str) -> str:
        seller_data = await self._request_ml(
            "GET",
            "/users/me",
            user_id=user_id,
        )
        if not isinstance(seller_data, dict) or not seller_data.get("id"):
            raise HTTPException(
                status_code=500,
                detail="No se pudo obtener el seller_id de Mercado Libre.",
            )
        return str(seller_data["id"])

    async def _request_ml(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._read_rate_limiter is None:
            self.start_request_context()

        return await ml_client.request(
            method,
            path,
            params=params,
            user_id=user_id,
            rate_limiter=self._read_rate_limiter,
        )

    async def _scan_all_item_ids(
        self,
        *,
        seller_id: str,
        user_id: str,
    ) -> list[str]:
        data = await self._request_ml(
            "GET",
            f"/users/{seller_id}/items/search",
            user_id=user_id,
            params={
                "search_type": "scan",
                "limit": self.SCAN_LIMIT,
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("Mercado Libre devolvió un scan inicial inválido.")

        expected_total = int((data.get("paging") or {}).get("total") or 0)
        collected_ids: list[str] = []
        seen_ids: set[str] = set()
        self._append_unique_ids(data.get("results"), collected_ids, seen_ids)

        scroll_id = str(data.get("scroll_id") or "")
        scan_pages = 1
        max_expected_pages = (
            math.ceil(expected_total / self.SCAN_LIMIT) + 10
            if expected_total > 0
            else self.MAX_SCAN_PAGES
        )
        max_pages = min(max_expected_pages, self.MAX_SCAN_PAGES)

        publication_sync_store.update(
            expected_count=expected_total,
            scanned_count=len(collected_ids),
            scan_pages=scan_pages,
            message=(
                f"Listadas {len(collected_ids)}/{expected_total or '?'} publicaciones..."
            ),
        )

        while scroll_id and scan_pages < max_pages:
            if expected_total > 0 and len(collected_ids) >= expected_total:
                break

            next_data = await self._request_ml(
                "GET",
                f"/users/{seller_id}/items/search",
                user_id=user_id,
                params={
                    "search_type": "scan",
                    "scroll_id": scroll_id,
                    "limit": self.SCAN_LIMIT,
                },
            )
            if not isinstance(next_data, dict):
                raise RuntimeError("Mercado Libre devolvió una página scan inválida.")

            page_results = next_data.get("results")
            if not page_results:
                break

            before_count = len(collected_ids)
            self._append_unique_ids(page_results, collected_ids, seen_ids)
            scan_pages += 1

            next_scroll_id = next_data.get("scroll_id")
            if next_scroll_id:
                scroll_id = str(next_scroll_id)

            publication_sync_store.update(
                expected_count=expected_total,
                scanned_count=len(collected_ids),
                scan_pages=scan_pages,
                progress=min(
                    14,
                    int((len(collected_ids) / max(expected_total, 1)) * 14),
                ),
                message=(
                    f"Listadas {len(collected_ids)}/{expected_total or '?'} "
                    "publicaciones..."
                ),
            )

            if len(collected_ids) == before_count:
                raise RuntimeError(
                    "El scan de Mercado Libre repitió una página sin avanzar."
                )

        if expected_total > 0 and len(collected_ids) < expected_total:
            raise RuntimeError(
                f"Scan incompleto: Mercado Libre informó {expected_total} publicaciones "
                f"y se obtuvieron {len(collected_ids)}."
            )

        return collected_ids[:expected_total] if expected_total > 0 else collected_ids

    @staticmethod
    def _append_unique_ids(
        results: Any,
        collected_ids: list[str],
        seen_ids: set[str],
    ) -> None:
        if not isinstance(results, list):
            return

        for raw_item_id in results:
            item_id = str(raw_item_id or "").strip()
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            collected_ids.append(item_id)

    async def _fetch_multiget_chunk(
        self,
        *,
        chunk: list[str],
        user_id: str,
        seller_id: str,
        sync_run_id: str,
    ) -> tuple[list[dict[str, Any]], int]:
        response = await self._request_ml(
            "GET",
            "/items",
            user_id=user_id,
            params={
                "ids": ",".join(chunk),
                "attributes": self.MULTIGET_ATTRIBUTES,
            },
        )
        if not isinstance(response, list):
            raise RuntimeError("Mercado Libre devolvió un multiget inválido.")

        rows: list[dict[str, Any]] = []
        failed_count = 0
        synchronized_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for result in response:
            if (
                not isinstance(result, dict)
                or str(result.get("code") or "") != "200"
            ):
                failed_count += 1
                continue

            body = result.get("body")
            if not isinstance(body, dict) or not body.get("id"):
                failed_count += 1
                continue

            rows.append(
                {
                    "seller_id": int(seller_id),
                    "mlc": str(body["id"]),
                    "sku": self._extract_sku(body),
                    "titulo": str(body.get("title") or ""),
                    "fecha_creacion": self._extract_creation_date(
                        body.get("date_created")
                    ),
                    "sync_run_id": sync_run_id,
                    "sincronizado_at": synchronized_at,
                }
            )

        missing_results = max(0, len(chunk) - len(response))
        return rows, failed_count + missing_results

    @staticmethod
    def _extract_sku(item: dict[str, Any]) -> str | None:
        attributes = item.get("attributes")
        if not isinstance(attributes, list):
            return None

        for attribute in attributes:
            if not isinstance(attribute, dict) or attribute.get("id") != "SELLER_SKU":
                continue

            value = str(attribute.get("value_name") or "").strip()
            if value:
                return value

        return None

    @staticmethod
    def _extract_creation_date(raw_value: Any) -> str | None:
        date_text = str(raw_value or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            return None

        try:
            date.fromisoformat(date_text)
        except ValueError:
            return None

        return date_text


publication_sync_service = PublicationSyncService()
