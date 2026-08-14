import asyncio
import logging
import random
import time
from typing import Any

import httpx
from fastapi import HTTPException

from config import settings
from services.excel_service import normalize_for_compare
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.token_store import require_ml_env, token_store

logger = logging.getLogger(__name__)


def _parse_retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return None

    for header_name in ("x-ratelimit-reset", "x-rate-limit-reset"):
        header_value = response.headers.get(header_name)
        if not header_value:
            continue
        try:
            reset_at = float(header_value)
            now = time.time()
            if reset_at > now:
                return max(0.0, reset_at - now)
        except ValueError:
            continue

    return None


def _is_retryable_unknown_forbidden(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False

    try:
        payload = response.json()
    except (TypeError, ValueError):
        return False

    if not isinstance(payload, dict):
        return False

    return (
        str(payload.get("message") or "").strip().lower() == "unknown_error"
        and str(payload.get("error") or "").strip().lower() == "forbidden"
    )


class MercadoLibreClient:
    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        if self.client is not None:
            return

        timeout = httpx.Timeout(
            settings.ml_http_timeout,
            connect=10.0,
            read=settings.ml_http_timeout,
            write=30.0,
            pool=10.0,
        )
        limits = httpx.Limits(
            max_connections=settings.ml_http_max_connections,
            max_keepalive_connections=settings.ml_http_max_keepalive,
        )

        self.client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            http2=False,
            headers={"Accept": "application/json"},
        )

    async def shutdown(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def _build_headers(self, access_token: str, has_json_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        if has_json_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        access_token: str | None = None,
        json_body: dict | None = None,
        params: dict | None = None,
        user_id: int | str | None = None,
        rate_limiter: Any | None = None,
    ) -> Any:
        if not self.client:
            raise RuntimeError("MercadoLibreClient no inicializado")

        url = f"{settings.ml_api_base}{path}"
        retryable_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        refreshed_after_401 = False

        # Cuando tenemos user_id, siempre resolvemos el token vigente usando
        # el margen preventivo configurado antes de cada request.
        if user_id is not None:
            access_token = await self.get_valid_token(user_id)

        if not access_token:
            raise HTTPException(status_code=401, detail="No hay access_token disponible")

        for attempt in range(1, settings.ml_retry_attempts + 1):
            try:
                if rate_limiter is not None:
                    await rate_limiter.acquire()

                headers = self._build_headers(
                    access_token=access_token,
                    has_json_body=json_body is not None,
                )

                response = await self.client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_body,
                    params=params,
                )
                retryable_unknown_forbidden = (
                    method.upper() in {"GET", "HEAD"}
                    and _is_retryable_unknown_forbidden(response)
                )

                if response.status_code == 401:
                    if user_id is not None and not refreshed_after_401:
                        refreshed_after_401 = True
                        token_data = await self.refresh_token(user_id)
                        access_token = token_data.get("access_token")
                        if not access_token:
                            raise HTTPException(
                                status_code=401,
                                detail="No se pudo renovar access_token tras 401",
                            )
                        continue

                    raise HTTPException(
                        status_code=401,
                        detail="Token inválido o expirado",
                    )

                if (
                    response.status_code in retryable_status
                    or retryable_unknown_forbidden
                ):
                    retry_after_seconds = _parse_retry_after_seconds(response)
                    response_payload = {
                        "message": f"ML API error {response.status_code}: {response.text}",
                        "status_code": response.status_code,
                        "retry_after_seconds": retry_after_seconds,
                        "retryable": True,
                        "retry_reason": (
                            "unknown_forbidden"
                            if retryable_unknown_forbidden
                            else "http_status"
                        ),
                    }

                    logger.warning(
                        "[ML_CLIENT][RETRYABLE] method=%s url=%s status=%s attempt=%s/%s retry_after=%s",
                        method,
                        url,
                        response.status_code,
                        attempt,
                        settings.ml_retry_attempts,
                        retry_after_seconds,
                    )

                    if response.status_code == 429:
                        base_delay = max(
                            float(getattr(settings, "ml_retry_429_min_delay_seconds", 12.0)),
                            retry_after_seconds or 0.0,
                        )
                        limiter_cooldown = max(
                            float(
                                getattr(
                                    settings,
                                    "ml_retry_429_cooldown_seconds",
                                    30.0,
                                )
                            ),
                            base_delay,
                        )
                        if (
                            rate_limiter is not None
                            and hasattr(rate_limiter, "penalize")
                        ):
                            await rate_limiter.penalize(limiter_cooldown)
                    elif retryable_unknown_forbidden:
                        base_delay = max(
                            float(
                                getattr(
                                    settings,
                                    "ml_retry_unknown_403_min_delay_seconds",
                                    5.0,
                                )
                            ),
                            settings.ml_retry_base_delay * (2 ** (attempt - 1)),
                        )
                        limiter_cooldown = max(
                            float(
                                getattr(
                                    settings,
                                    "ml_retry_unknown_403_cooldown_seconds",
                                    15.0,
                                )
                            ),
                            base_delay,
                        )
                        if (
                            rate_limiter is not None
                            and hasattr(rate_limiter, "penalize")
                        ):
                            await rate_limiter.penalize(limiter_cooldown)
                    else:
                        base_delay = settings.ml_retry_base_delay * (2 ** (attempt - 1))

                    if attempt == settings.ml_retry_attempts:
                        raise HTTPException(
                            status_code=response.status_code,
                            detail=response_payload,
                        )

                    if response.status_code == 429 and retry_after_seconds:
                        delay = base_delay
                    else:
                        delay = min(
                            base_delay,
                            float(
                                getattr(
                                    settings,
                                    "ml_retry_max_delay_seconds",
                                    60.0,
                                )
                            ),
                        )
                    delay += random.uniform(0, 0.5)
                    await asyncio.sleep(delay)
                    continue

                if response.status_code >= 400:
                    logger.warning(
                        "[ML_CLIENT][ERROR] method=%s url=%s status=%s body=%s",
                        method,
                        url,
                        response.status_code,
                        response.text,
                    )
                    raise HTTPException(
                        status_code=response.status_code,
                        detail={
                            "message": f"ML API error {response.status_code}: {response.text}",
                            "status_code": response.status_code,
                            "retry_after_seconds": _parse_retry_after_seconds(response),
                        },
                    )

                if not response.content:
                    return {}

                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type.lower():
                    return response.json()

                return {"raw_response": response.text}

            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            ) as exc:
                last_error = exc

                if attempt == settings.ml_retry_attempts:
                    break

                delay = min(
                    settings.ml_retry_base_delay * (2 ** (attempt - 1)),
                    8,
                ) + random.uniform(0, 0.3)
                await asyncio.sleep(delay)

            except httpx.HTTPError as exc:
                last_error = exc

                if attempt == settings.ml_retry_attempts:
                    break

                delay = min(
                    settings.ml_retry_base_delay * (2 ** (attempt - 1)),
                    8,
                ) + random.uniform(0, 0.3)
                await asyncio.sleep(delay)

        raise HTTPException(
            status_code=502,
            detail=f"Error de red contra Mercado Libre: {last_error}",
        )

    async def validate_token(self, access_token: str) -> bool:
        if not self.client or not access_token:
            return False
        try:
            r = await self.client.get(
                settings.ml_me_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            return r.status_code == 200
        except Exception:
            return False

    async def refresh_token(self, user_id: int | str) -> dict:
        require_ml_env()

        token_data = token_store.get(user_id)
        if not token_data:
            raise HTTPException(
                status_code=404,
                detail="No hay token guardado para ese user_id",
            )

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            token_store.remove(user_id)
            await supabase_meli_connection_store.mark_disconnected(user_id)
            raise HTTPException(
                status_code=400,
                detail="No hay refresh_token guardado",
            )

        if not self.client:
            raise RuntimeError("MercadoLibreClient no inicializado")

        payload = {
            "grant_type": "refresh_token",
            "client_id": settings.ml_client_id,
            "client_secret": settings.ml_client_secret,
            "refresh_token": refresh_token,
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
        }

        r = await self.client.post(settings.ml_token_url, data=payload, headers=headers)
        if r.status_code >= 400:
            token_store.remove(user_id)
            await supabase_meli_connection_store.mark_disconnected(user_id)
            raise HTTPException(status_code=r.status_code, detail=r.text)

        new_token_data = token_store.build_payload(r.json(), user_id)
        token_store.set(user_id, new_token_data)
        await supabase_meli_connection_store.sync_connection(
            new_token_data,
            is_active=True,
        )
        return new_token_data

    async def get_valid_token(self, user_id: int | str) -> str:
        token_data = token_store.get(user_id)
        if not token_data:
            raise HTTPException(status_code=404, detail="No hay token guardado")

        access_token = token_data.get("access_token")
        expires_at = int(token_data.get("expires_at", 0))
        now = int(time.time())

        refresh_margin = int(getattr(settings, "token_refresh_margin_seconds", 600))

        if not access_token or now >= (expires_at - refresh_margin):
            token_data = await self.refresh_token(user_id)
            access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No se pudo obtener access_token válido",
            )

        return access_token

    async def get_item_detail(
        self,
        access_token: str | None,
        item_id: str,
        user_id: int | str | None = None,
    ) -> dict:
        data = await self.request(
            "GET",
            f"/items/{item_id}",
            access_token=access_token,
            user_id=user_id,
        )
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=500,
                detail=f"Respuesta inválida para item {item_id}",
            )
        return data

    async def search_items_by_seller_sku(
        self,
        access_token: str | None,
        seller_user_id: int | str,
        seller_sku: str,
        user_id: int | str | None = None,
    ) -> dict:
        data = await self.request(
            "GET",
            f"/users/{seller_user_id}/items/search",
            access_token=access_token,
            params={"seller_sku": seller_sku},
            user_id=user_id,
        )
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=500,
                detail=f"Respuesta inválida buscando publicaciones para seller_sku {seller_sku}",
            )
        return data

    async def get_item_description(
        self,
        access_token: str | None,
        item_id: str,
        user_id: int | str | None = None,
    ) -> dict:
        data = await self.request(
            "GET",
            f"/items/{item_id}/description",
            access_token=access_token,
            user_id=user_id,
        )
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=500,
                detail=f"Respuesta inválida para descripción de item {item_id}",
            )
        return data

    async def get_top_values(
        self,
        access_token: str | None,
        attribute_id: str,
        known_attributes: list[dict] | None = None,
        user_id: int | str | None = None,
    ) -> list[dict]:
        payload: dict[str, Any] = {}
        if known_attributes:
            payload["known_attributes"] = known_attributes

        response = await self.request(
            "POST",
            f"/catalog_domains/MLC-CARS_AND_VANS_FOR_COMPATIBILITIES/attributes/{attribute_id}/top_values",
            access_token=access_token,
            json_body=payload,
            user_id=user_id,
        )

        if isinstance(response, list):
            return [x for x in response if isinstance(x, dict)]

        if isinstance(response, dict):
            top_values = response.get("top_values")
            if isinstance(top_values, list):
                return [x for x in top_values if isinstance(x, dict)]

            results = response.get("results")
            if isinstance(results, list):
                return [x for x in results if isinstance(x, dict)]

            values = response.get("values")
            if isinstance(values, list):
                return [x for x in values if isinstance(x, dict)]

        return []

    async def count_vehicle_family_products(
        self,
        access_token: str | None = None,
        attributes: list[dict[str, Any]] | None = None,
        domain_id: str | None = None,
        user_id: int | str | None = None,
    ) -> int:
        response = await self.request(
            "POST",
            "/catalog_compatibilities/products_search/count_family_products",
            access_token=access_token,
            json_body={
                "domain_id": domain_id or settings.ml_domain_id,
                "attributes": attributes or [],
            },
            user_id=user_id,
        )

        if isinstance(response, dict):
            try:
                return max(0, int(response.get("count", 0) or 0))
            except (TypeError, ValueError):
                return 0

        return 0

    async def add_user_product_compatibility(
        self,
        access_token: str | None,
        user_product_id: str,
        category_id: str,
        product_id: str,
        creation_source: str = "DEFAULT",
        user_id: int | str | None = None,
    ) -> dict:
        body = {
            "domain_id": settings.ml_domain_id,
            "category_id": category_id,
            "products": [
                {
                    "id": str(product_id),
                    "creation_source": creation_source,
                }
            ],
        }

        data = await self.request(
            "POST",
            f"/user-products/{user_product_id}/compatibilities",
            access_token=access_token,
            json_body=body,
            user_id=user_id,
        )
        return data if isinstance(data, dict) else {"raw_response": data}

    async def add_user_product_compatibilities_batch(
        self,
        access_token: str | None,
        user_product_id: str,
        category_id: str,
        product_ids: list[str],
        restrictions: list | None = None,
        user_id: int | str | None = None,
        note: str = "DEBES CONSULTAR OBLIGATORIAMENTE CON CHASIS Y CARACTERISTICAS DEL VEHICULO PARA CORROBORAR APLICACION",
    ) -> dict:
        if not product_ids:
            return {"results": []}

        resolved_restrictions = restrictions if restrictions is not None else []

        products_list = []
        for product_id in product_ids:
            product_entry: dict[str, Any] = {
                "id": str(product_id),
                "creation_source": "DEFAULT",
                "note": note,
            }
            if resolved_restrictions:
                product_entry["restrictions"] = resolved_restrictions
            products_list.append(product_entry)

        body = {
            "domain_id": settings.ml_domain_id,
            "category_id": category_id,
            "products": products_list,
        }

        import json as _json
        #print("=" * 60)
        #print(f"[DEBUG] POST /user-products/{user_product_id}/compatibilities")
        #print(f"[DEBUG] BODY:\n{_json.dumps(body, indent=2, ensure_ascii=False)}")
        #print("=" * 60)

        data = await self.request(
            "POST",
            f"/user-products/{user_product_id}/compatibilities",
            access_token=access_token,
            json_body=body,
            user_id=user_id,
        )

        #print(f"[DEBUG] RESPONSE:\n{_json.dumps(data if isinstance(data, dict) else {'raw': str(data)}, indent=2, ensure_ascii=False)}")
        #print("=" * 60)

        return data if isinstance(data, dict) else {"raw_response": data}

    async def add_user_product_compatibility_families_batch(
        self,
        access_token: str | None,
        user_product_id: str,
        category_id: str,
        product_families: list[dict[str, Any]],
        user_id: int | str | None = None,
    ) -> dict:
        if not product_families:
            return {"created_compatibilities_count": 0}

        body = {
            "domain_id": settings.ml_domain_id,
            "category_id": category_id,
            "products_families": product_families,
        }

        data = await self.request(
            "POST",
            f"/user-products/{user_product_id}/compatibilities",
            access_token=access_token,
            json_body=body,
            user_id=user_id,
        )

        return data if isinstance(data, dict) else {"raw_response": data}

    async def update_item_price_stock(
        self,
        access_token: str | None,
        item_id: str,
        price: float | None = None,
        available_quantity: int | None = None,
        status: str | None = None,
        user_id: int | str | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if price is not None:
            body["price"] = int(price)
        if available_quantity is not None:
            body["available_quantity"] = int(available_quantity)
        if status is not None:
            body["status"] = status

        if not body:
            return {"ok": False, "reason": "No hay campos para actualizar"}

        data = await self.request(
            "PUT",
            f"/items/{item_id}",
            access_token=access_token,
            json_body=body,
            user_id=user_id,
        )

        return data if isinstance(data, dict) else {"raw_response": data}

    async def update_item_pictures(
        self,
        access_token: str | None,
        item_id: str,
        picture_urls: list[str] | None = None,
        user_id: int | str | None = None,
    ) -> dict:
        pictures = [
            {"source": str(url).strip()}
            for url in (picture_urls or [])
            if str(url).strip()
        ]

        if not pictures:
            return {"ok": False, "reason": "No hay fotos para actualizar"}

        data = await self.request(
            "PUT",
            f"/items/{item_id}",
            access_token=access_token,
            json_body={"pictures": pictures},
            user_id=user_id,
        )

        return data if isinstance(data, dict) else {"raw_response": data}

    async def add_item_compatibility_exception(
        self,
        access_token: str | None,
        item_id: str,
        comment: str,
        user_id: int | str | None = None,
    ) -> dict:
        data = await self.request(
            "POST",
            f"/items/{item_id}/compatibilities/exception",
            access_token=access_token,
            json_body={"comment": comment},
            user_id=user_id,
        )
        return data if isinstance(data, dict) else {"raw_response": data}

def extract_values_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        values = data.get("values")
        if isinstance(values, list):
            return [x for x in values if isinstance(x, dict)]

        results = data.get("results")
        if isinstance(results, list):
            return [x for x in results if isinstance(x, dict)]

        top_values = data.get("top_values")
        if isinstance(top_values, list):
            return [x for x in top_values if isinstance(x, dict)]

    return []


def pick_value_id_by_name(values: list[dict], wanted_name: str) -> str | None:
    wanted = normalize_for_compare(wanted_name)
    if not wanted:
        return None

    for item in values:
        name = normalize_for_compare(item.get("name"))
        if name == wanted:
            return str(item.get("id"))

    for item in values:
        name = normalize_for_compare(item.get("name"))
        if wanted in name:
            return str(item.get("id"))

    for item in values:
        name = normalize_for_compare(item.get("name"))
        if name and name in wanted:
            return str(item.get("id"))

    return None


ml_client = MercadoLibreClient()
