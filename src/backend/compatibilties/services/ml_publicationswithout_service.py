import asyncio
import time
from typing import Any
from io import BytesIO
from fastapi import HTTPException

from openpyxl import Workbook
from openpyxl.styles import Font

from services.ml_client import ml_client


class MlPublicationsService:
    MAX_PAGE_SIZE = 20
    SCAN_LIMIT = 100
    MULTIGET_CHUNK_SIZE = 20
    MULTIGET_CONCURRENCY = 16
    CACHE_TTL_SECONDS = 600
    STALE_WHILE_REVALIDATE_SECONDS = 1800

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._refresh_status: dict[str, dict[str, Any]] = {}

    async def get_publications_without_compatibilities(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        refresh: bool = False,
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page debe ser mayor o igual a 1")

        if page_size < 1 or page_size > self.MAX_PAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"page_size debe estar entre 1 y {self.MAX_PAGE_SIZE}",
            )

        access_token = await ml_client.get_valid_token(int(user_id))
        seller_id = await self._get_seller_id(access_token)
        seller_id_str = str(seller_id)

        if refresh:
            await self.start_background_refresh(user_id)

        all_items = await self._get_or_build_cache(
            seller_id=seller_id_str,
            access_token=access_token,
        )

        filtered_items=self._filter_items(all_items, q)


        result = self._paginate_items(filtered_items, page, page_size)

        cache_meta = self._cache.get(seller_id_str, {})
        refresh_meta = self._refresh_status.get(seller_id_str, {})

        result["cache_ttl_seconds"] = self.CACHE_TTL_SECONDS
        result["cache_size"] = len(all_items)
        result["search_applied"] = bool((q or "").strip())
        result["cache_generated_at"] = cache_meta.get("generated_at")
        result["cache_expires_at"] = cache_meta.get("expires_at")
        result["cache_state"] = cache_meta.get("state", "unknown")
        result["refresh_in_progress"] = bool(refresh_meta.get("in_progress", False))
        result["last_refresh_started_at"] = refresh_meta.get("started_at")
        result["last_refresh_finished_at"] = refresh_meta.get("finished_at")
        result["last_refresh_error"] = refresh_meta.get("error")

        return result
    
    async def export_publications_without_compatibilities_excel(
        self,
        user_id: str,
        q: str = "",
    ) -> tuple[BytesIO, str]:
        access_token = await ml_client.get_valid_token(int(user_id))
        seller_id = await self._get_seller_id(access_token)
        seller_id_str = str(seller_id)

        all_items = await self._get_or_build_cache(
            seller_id=seller_id_str,
            access_token=access_token,
        )

        filtered_items = self._filter_items(all_items, q)

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Sin compatibilidades"

        worksheet.append(["MLC", "Título"])

        bold_font = Font(bold=True)
        worksheet["A1"].font = bold_font
        worksheet["B1"].font = bold_font

        for item in filtered_items:
            worksheet.append([
                str(item.get("mlc") or "-"),
                str(item.get("title") or "-"),
            ])

        worksheet.column_dimensions["A"].width = 22
        worksheet.column_dimensions["B"].width = 90

        output = BytesIO()
        workbook.save(output)
        output.seek(0)

        safe_suffix = self._build_safe_filename_suffix(q)
        filename = (
            f"publicaciones_sin_compatibilidades_{safe_suffix}.xlsx"
            if safe_suffix
            else "publicaciones_sin_compatibilidades.xlsx"
        )

        return output, filename

    async def start_background_refresh(self, user_id: str) -> dict[str, Any]:
        access_token = await ml_client.get_valid_token(int(user_id))
        seller_id = str(await self._get_seller_id(access_token))

        existing = self._refresh_tasks.get(seller_id)
        if existing and not existing.done():
            return {
                "ok": True,
                "message": "Ya hay una actualización en progreso",
                "in_progress": True,
            }

        self._schedule_background_refresh(seller_id, access_token)
        return {
            "ok": True,
            "message": "Actualización iniciada",
            "in_progress": True,
        }

    async def get_refresh_status(self, user_id: str) -> dict[str, Any]:
        access_token = await ml_client.get_valid_token(int(user_id))
        seller_id = str(await self._get_seller_id(access_token))

        meta = self._refresh_status.get(seller_id, {})
        return {
            "in_progress": bool(meta.get("in_progress", False)),
            "started_at": meta.get("started_at"),
            "finished_at": meta.get("finished_at"),
            "error": meta.get("error"),
        }

    async def _get_seller_id(self, access_token: str) -> int:
        seller_data = await ml_client.request("GET", "/users/me", access_token)
        seller_id = seller_data.get("id")
        if not seller_id:
            raise HTTPException(status_code=500, detail="No se pudo obtener el seller_id")
        return int(seller_id)

    def _filter_items(
        self,
        items: list[dict[str, str]],
        q: str = "",
    ) -> list[dict[str, str]]:
        query = (q or "").strip().lower()

        if not query:
            return items

        return [
            item
            for item in items
            if query in str(item.get("mlc", "")).lower()
            or query in str(item.get("title", "")).lower()
        ]

    def _build_safe_filename_suffix(self, q: str) -> str:
        raw = (q or "").strip()
        if not raw:
            return ""

        cleaned = "".join(
            ch if ch.isalnum() or ch in ("_", "-") else "_"
            for ch in raw.replace(" ", "_")
        )

        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")

        return cleaned.strip("_")

    async def _get_or_build_cache(
        self,
        seller_id: str,
        access_token: str,
    ) -> list[dict[str, str]]:
        now = time.time()
        cached = self._cache.get(seller_id)

        if (
            cached
            and cached.get("expires_at", 0) > now
            and isinstance(cached.get("items"), list)
        ):
            return cached["items"]

        if (
            cached
            and cached.get("stale_expires_at", 0) > now
            and isinstance(cached.get("items"), list)
        ):
            self._schedule_background_refresh(seller_id, access_token)
            return cached["items"]

        lock = self._locks.setdefault(seller_id, asyncio.Lock())

        async with lock:
            now = time.time()
            cached = self._cache.get(seller_id)

            if (
                cached
                and cached.get("expires_at", 0) > now
                and isinstance(cached.get("items"), list)
            ):
                return cached["items"]

            return await self._rebuild_cache(seller_id, access_token)

    def _schedule_background_refresh(self, seller_id: str, access_token: str) -> None:
        existing = self._refresh_tasks.get(seller_id)
        if existing and not existing.done():
            return

        async def runner():
            self._refresh_status[seller_id] = {
                "in_progress": True,
                "started_at": time.time(),
                "finished_at": None,
                "error": None,
            }
            try:
                lock = self._locks.setdefault(seller_id, asyncio.Lock())
                async with lock:
                    await self._rebuild_cache(seller_id, access_token)
                self._refresh_status[seller_id]["finished_at"] = time.time()
            except Exception as exc:
                self._refresh_status[seller_id]["error"] = repr(exc)
                self._refresh_status[seller_id]["finished_at"] = time.time()
                print(f"[ml_publications_service] background refresh error seller={seller_id}: {exc!r}")
            finally:
                self._refresh_status[seller_id]["in_progress"] = False
                self._refresh_tasks.pop(seller_id, None)

        self._refresh_tasks[seller_id] = asyncio.create_task(runner())

    async def _rebuild_cache(
        self,
        seller_id: str,
        access_token: str,
    ) -> list[dict[str, str]]:
        started_at = time.time()

        old_cache = self._cache.get(seller_id, {})
        old_items = old_cache.get("items", []) if isinstance(old_cache.get("items"), list) else []
        old_by_mlc = {item["mlc"]: item for item in old_items if item.get("mlc")}

        mlc_ids = await self._scan_incomplete_compatibility_ids(
            seller_id=seller_id,
            access_token=access_token,
        )

        current_mlc_set = set(mlc_ids)

        # Reutilizar títulos ya cacheados
        reused_items = []
        missing_ids = []

        for mlc in mlc_ids:
            cached_item = old_by_mlc.get(mlc)
            if cached_item:
                reused_items.append({
                    "mlc": cached_item["mlc"],
                    "title": cached_item.get("title", ""),
                })
            else:
                missing_ids.append(mlc)

        # Solo pedir a ML los títulos nuevos/faltantes
        fetched_items = await self._get_items_titles_multiget_concurrent(
            mlc_ids=missing_ids,
            access_token=access_token,
        )

        fetched_by_mlc = {item["mlc"]: item for item in fetched_items if item.get("mlc")}

        # Reconstruir respetando el orden del scan actual
        final_items: list[dict[str, str]] = []
        seen: set[str] = set()

        for mlc in mlc_ids:
            item = old_by_mlc.get(mlc) or fetched_by_mlc.get(mlc)
            if not item or mlc in seen:
                continue
            seen.add(mlc)
            final_items.append({
                "mlc": str(item["mlc"]),
                "title": str(item.get("title", "")),
            })

        now = time.time()
        self._cache[seller_id] = {
            "generated_at": started_at,
            "expires_at": now + self.CACHE_TTL_SECONDS,
            "stale_expires_at": now + self.STALE_WHILE_REVALIDATE_SECONDS,
            "items": final_items,
            "state": "fresh",
        }

        print(
            f"[ml_publications_service] cache rebuilt seller={seller_id} "
            f"items={len(final_items)} reused={len(reused_items)} fetched_new={len(missing_ids)} "
            f"removed={len([k for k in old_by_mlc.keys() if k not in current_mlc_set])} "
            f"elapsed={round(now - started_at, 2)}s"
        )

        return final_items

    async def _scan_incomplete_compatibility_ids(
        self,
        seller_id: str,
        access_token: str,
    ) -> list[str]:
        collected_ids: list[str] = []
        seen_ids: set[str] = set()
        seen_scroll_ids: set[str] = set()

        data = await ml_client.request(
            "GET",
            f"/users/{seller_id}/items/search",
            access_token,
            params={
                "search_type": "scan",
                "tags": "incomplete_compatibilities",
                "limit": self.SCAN_LIMIT,
            },
        )

        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail="Respuesta inválida en scan inicial")

        paging = data.get("paging") or {}
        expected_total = int(paging.get("total", 0))

        first_results = data.get("results") or []
        for item_id in first_results:
            if isinstance(item_id, str) and item_id not in seen_ids:
                seen_ids.add(item_id)
                collected_ids.append(item_id)

        scroll_id = data.get("scroll_id")
        if scroll_id:
            seen_scroll_ids.add(scroll_id)

        while scroll_id:
            next_data = await ml_client.request(
                "GET",
                f"/users/{seller_id}/items/search",
                access_token,
                params={
                    "search_type": "scan",
                    "scroll_id": scroll_id,
                },
            )

            if not isinstance(next_data, dict):
                break

            page_results = next_data.get("results") or []
            next_scroll_id = next_data.get("scroll_id")

            if not page_results:
                break

            before_count = len(collected_ids)

            for item_id in page_results:
                if isinstance(item_id, str) and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    collected_ids.append(item_id)

            after_count = len(collected_ids)

            if after_count == before_count:
                break

            if expected_total > 0 and len(collected_ids) >= expected_total:
                collected_ids = collected_ids[:expected_total]
                break

            if not next_scroll_id or next_scroll_id in seen_scroll_ids:
                break

            seen_scroll_ids.add(next_scroll_id)
            scroll_id = next_scroll_id

        if expected_total > 0 and len(collected_ids) > expected_total:
            collected_ids = collected_ids[:expected_total]

        return collected_ids

    async def _get_items_titles_multiget_concurrent(
        self,
        mlc_ids: list[str],
        access_token: str,
    ) -> list[dict[str, str]]:
        if not mlc_ids:
            return []

        semaphore = asyncio.Semaphore(self.MULTIGET_CONCURRENCY)
        chunks = [
            mlc_ids[i : i + self.MULTIGET_CHUNK_SIZE]
            for i in range(0, len(mlc_ids), self.MULTIGET_CHUNK_SIZE)
        ]

        async def fetch_chunk(chunk: list[str]) -> list[dict[str, str]]:
            async with semaphore:
                response = await ml_client.request(
                    "GET",
                    "/items",
                    access_token,
                    params={
                        "ids": ",".join(chunk),
                        "attributes": "id,title",
                    },
                )

                parsed_items: list[dict[str, str]] = []
                if not isinstance(response, list):
                    return parsed_items

                for row in response:
                    if not isinstance(row, dict):
                        continue
                    if row.get("code") != 200:
                        continue

                    body = row.get("body") or {}
                    item_id = body.get("id")
                    title = body.get("title")

                    if item_id:
                        parsed_items.append(
                            {
                                "mlc": str(item_id),
                                "title": str(title or ""),
                            }
                        )
                return parsed_items

        results = await asyncio.gather(*(fetch_chunk(chunk) for chunk in chunks))

        items: list[dict[str, str]] = []
        for batch in results:
            items.extend(batch)

        return items

    def _paginate_items(
        self,
        items: list[dict[str, str]],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        total = len(items)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        start = (page - 1) * page_size
        end = start + page_size
        paged_items = items[start:end]

        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "items": paged_items,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        }

    def invalidate_cache(self, seller_id: str | None = None) -> None:
        if seller_id:
            seller_id = str(seller_id)
            self._cache.pop(seller_id, None)
            self._locks.pop(seller_id, None)
            task = self._refresh_tasks.pop(seller_id, None)
            if task and not task.done():
                task.cancel()
            self._refresh_status.pop(seller_id, None)
            return

        self._cache.clear()
        self._locks.clear()
        self._refresh_status.clear()
        for _, task in self._refresh_tasks.items():
            if not task.done():
                task.cancel()
        self._refresh_tasks.clear()

    


ml_publications_service = MlPublicationsService()
