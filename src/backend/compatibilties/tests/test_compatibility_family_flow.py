import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import settings
from services.compatibility_batch_service import (
    build_grouped_product_families,
    chunk_product_families,
    process_compatibility_batches,
)
from services.compatibility_orchestrator_service import (
    process_excel_compatibilities_end_to_end,
)
from services.compatibility_service import (
    JobCaches,
    JobMetrics,
    resolve_vehicle_product_row,
)
from services.ml_client import MercadoLibreClient
from services.process_chunking_service import (
    get_compatibility_chunk_pause_seconds,
    get_process_file_chunk_size,
)


def _family_row(
    *,
    item_id: str = "MLC123",
    family_key: str = "family-key",
    matched_products: int = 1,
) -> dict:
    return {
        "ok": True,
        "item_id": item_id,
        "brand_name": "CHEVROLET",
        "model_name": "SAIL",
        "year": 2018,
        "compatibility_mode": "product_family",
        "product_family_key": family_key,
        "family_product_count": matched_products,
        "product_family": {
            "domain_id": settings.ml_domain_id,
            "creation_source": "DEFAULT",
            "attributes": [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_id": "300"},
            ],
        },
        "familia": "",
        "posicion_dt": "",
        "posicion_id": "",
    }


class MercadoLibreCompatibilityClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_family_products_uses_current_official_resource(self):
        client = MercadoLibreClient()
        attributes = [{"id": "BRAND", "value_id": "100"}]

        with patch.object(
            client,
            "request",
            new=AsyncMock(return_value={"count": 7}),
        ) as request:
            count = await client.count_vehicle_family_products(
                access_token="token",
                attributes=attributes,
                domain_id=settings.ml_domain_id,
                user_id="123",
            )

        self.assertEqual(count, 7)
        request.assert_awaited_once_with(
            "POST",
            "/catalog_compatibilities/products_search/count_family_products",
            access_token="token",
            json_body={
                "domain_id": settings.ml_domain_id,
                "attributes": attributes,
            },
            user_id="123",
        )

    async def test_user_product_write_sends_product_families(self):
        client = MercadoLibreClient()
        product_families = [_family_row()["product_family"]]

        with patch.object(
            client,
            "request",
            new=AsyncMock(return_value={"created_compatibilities_count": 1}),
        ) as request:
            response = await client.add_user_product_compatibility_families_batch(
                access_token="token",
                user_product_id="MLCU123",
                category_id="MLC1748",
                product_families=product_families,
                user_id="123",
            )

        self.assertEqual(response["created_compatibilities_count"], 1)
        request.assert_awaited_once_with(
            "POST",
            "/user-products/MLCU123/compatibilities",
            access_token="token",
            json_body={
                "domain_id": settings.ml_domain_id,
                "category_id": "MLC1748",
                "products_families": product_families,
            },
            user_id="123",
        )


class CompatibilityFamilyResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog_cache = SimpleNamespace(
            resolve_brand_id=lambda value: "100",
            resolve_model_id=lambda value: "200",
            resolve_year_id=lambda value: "300",
            resolve_version_id=lambda value: "400",
            resolve_engine_id=lambda value: "500",
            resolve_transmission_id=lambda value: "600",
        )
        self.row = {
            "ASOCIACION ML": "MLC123",
            "MARCA": "Chevrolet",
            "MODELO": "Sail",
            "VERSION": "LT",
            "CILINDRADA": "1.5",
            "TRANSMISION": "Manual",
            "AÑO": 2018,
        }

    async def test_resolution_returns_family_instead_of_catalog_product_id(self):
        with patch(
            "services.compatibility_service.call_ml",
            new=AsyncMock(return_value=1),
        ) as call_ml:
            result = await resolve_vehicle_product_row(
                access_token="token",
                user_id="123",
                row=self.row,
                catalog_cache=self.catalog_cache,
                caches=JobCaches(),
                metrics=JobMetrics(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["compatibility_mode"], "product_family")
        self.assertNotIn("product_id", result)
        self.assertEqual(result["family_product_count"], 1)
        self.assertEqual(
            result["product_family"]["creation_source"],
            "DEFAULT",
        )
        self.assertEqual(
            call_ml.await_args.kwargs["domain_id"],
            settings.ml_domain_id,
        )

    async def test_resolution_rejects_family_above_official_200_limit(self):
        with patch(
            "services.compatibility_service.call_ml",
            new=AsyncMock(return_value=201),
        ):
            result = await resolve_vehicle_product_row(
                access_token="token",
                user_id="123",
                row=self.row,
                catalog_cache=self.catalog_cache,
                caches=JobCaches(),
                metrics=JobMetrics(),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error_code"],
            "PRODUCT_FAMILY_LIMIT_EXCEEDED",
        )


class CompatibilityFamilyBatchTests(unittest.IsolatedAsyncioTestCase):
    def test_batches_are_capped_by_records_and_matched_products(self):
        entries = [
            {
                "compatibility_key": f"family:{index}",
                "payload": {},
                "matched_products_count": 1,
            }
            for index in range(101)
        ]
        batches = list(chunk_product_families(entries, batch_size=100))
        self.assertEqual(
            [len(batch) for batch in batches],
            ([10] * 10) + [1],
        )

        count_limited_entries = [
            {
                "compatibility_key": "family:a",
                "payload": {},
                "matched_products_count": 120,
            },
            {
                "compatibility_key": "family:b",
                "payload": {},
                "matched_products_count": 90,
            },
            {
                "compatibility_key": "family:c",
                "payload": {},
                "matched_products_count": 10,
            },
        ]
        count_limited_batches = list(
            chunk_product_families(
                count_limited_entries,
                batch_size=100,
            )
        )
        self.assertEqual(
            [len(batch) for batch in count_limited_batches],
            [1, 2],
        )

    def test_grouping_adds_required_creation_source_note_and_restrictions(self):
        row = _family_row()
        row.update(
            {
                "familia": "MLC-VEHICLE_HEADLIGHTS",
                "posicion_dt": "Delantera",
                "posicion_id": "Izquierda",
            }
        )

        grouped = build_grouped_product_families([row])
        payload = grouped["MLC123"][0]["payload"]

        self.assertEqual(payload["creation_source"], "DEFAULT")
        self.assertTrue(payload["note"])
        self.assertTrue(payload["restrictions"])

    async def test_batch_process_writes_to_user_product_and_maps_success(self):
        rows = [
            _family_row(family_key="a"),
            _family_row(family_key="b"),
        ]
        on_progress = AsyncMock()

        with (
            patch(
                "services.compatibility_batch_service.get_item_compact_cached",
                new=AsyncMock(
                    return_value={
                        "category_id": "MLC1748",
                        "user_product_id": "MLCU123",
                    }
                ),
            ),
            patch(
                "services.compatibility_batch_service.call_ml",
                new=AsyncMock(
                    return_value={"created_compatibilities_count": 2}
                ),
            ) as call_ml,
        ):
            outcome = await process_compatibility_batches(
                access_token="token",
                user_id="123",
                rows=rows,
                on_progress=on_progress,
            )

        self.assertEqual(call_ml.await_count, 1)
        self.assertEqual(
            len(call_ml.await_args.kwargs["product_families"]),
            2,
        )
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertEqual(
            outcome["summary"]["total_created_compatibilities"],
            2,
        )
        on_progress.assert_awaited_once_with(1, 1, 2)


class CompatibilityChunkPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_default_policy_is_100_rows_and_210_seconds(self):
        self.assertEqual(get_process_file_chunk_size(), 100)
        self.assertEqual(get_compatibility_chunk_pause_seconds(), 210)
        self.assertEqual(settings.compat_batch_size, 100)

    async def test_orchestrator_pauses_between_100_row_chunks(self):
        rows = [{"row": index} for index in range(101)]
        empty_resolution_summary = {
            "processed_rows": 0,
            "success_count": 0,
            "error_count": 0,
            "functional_errors": 0,
            "technical_errors": 0,
            "compatibilities_total": 0,
            "compatibilities_ok": 0,
            "compatibilities_error": 0,
        }

        catalog_data = SimpleNamespace(stats=lambda: {})
        catalog_cache = SimpleNamespace(
            preload_all=AsyncMock(return_value=catalog_data)
        )

        async def resolve_chunk(**kwargs):
            return {
                "results": kwargs["rows"],
                "summary": dict(empty_resolution_summary),
            }

        async def apply_chunk(**kwargs):
            await kwargs["on_progress"](1, 1, 3)
            return {
                "results": kwargs["rows"],
                "batch_results": [
                    {
                        "ok": True,
                        "created_compatibilities_count": 3,
                    }
                ],
                "summary": {
                    "total_created_compatibilities": 3,
                },
            }

        with (
            patch(
                "services.compatibility_orchestrator_service.build_vehicle_resolution_plan",
                return_value=([{}] * 101, []),
            ),
            patch(
                "services.compatibility_orchestrator_service.CatalogPreloadService",
                return_value=catalog_cache,
            ),
            patch(
                "services.compatibility_orchestrator_service.process_rows_for_job",
                new=AsyncMock(side_effect=resolve_chunk),
            ) as process_rows,
            patch(
                "services.compatibility_orchestrator_service.process_compatibility_batches",
                new=AsyncMock(side_effect=apply_chunk),
            ),
            patch(
                "services.compatibility_orchestrator_service.JobStore.update",
            ) as update_job,
            patch(
                "services.compatibility_orchestrator_service.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            await process_excel_compatibilities_end_to_end(
                job_id="job",
                access_token="token",
                user_id="123",
                rows=rows,
            )

        self.assertEqual(
            [len(call.kwargs["rows"]) for call in process_rows.await_args_list],
            [100, 1],
        )
        sleep.assert_awaited_once_with(210)
        self.assertTrue(
            any(
                call.kwargs.get("compatibilities_created") == 6
                for call in update_job.call_args_list
            )
        )
