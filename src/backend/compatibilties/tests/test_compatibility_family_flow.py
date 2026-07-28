import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_batch_service import (
    build_compat_summary,
    build_grouped_product_families,
    chunk_product_families,
    process_compatibility_batches,
    validate_resolved_family_rows,
)
from services.process_queue_service import _has_partial_process_errors
from services.compatibility_orchestrator_service import (
    process_excel_compatibilities_end_to_end,
)
from services.compatibility_service import (
    JobCaches,
    JobMetrics,
    build_vehicle_resolution_plan,
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
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
                {
                    "id": "TRANSMISSION_CONTROL_TYPE",
                    "value_id": "600",
                },
            ],
        },
        "category_id": "MLC1748",
        "user_product_id": "MLCU123",
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
        with (
            patch(
                "services.compatibility_service.get_item_detail_cached",
                new=AsyncMock(
                    return_value={
                        "category_id": "MLC1748",
                        "user_product_id": "MLCU123",
                    }
                ),
            ),
            patch(
                "services.compatibility_service.call_ml",
                new=AsyncMock(return_value=1),
            ) as call_ml,
        ):
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
        self.assertEqual(
            result["product_family"]["attributes"],
            [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_id": "300"},
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
                {
                    "id": "TRANSMISSION_CONTROL_TYPE",
                    "value_id": "600",
                },
            ],
        )
        self.assertEqual(result["category_id"], "MLC1748")
        self.assertEqual(result["user_product_id"], "MLCU123")

    async def test_resolution_rejects_family_above_official_200_limit(self):
        with (
            patch(
                "services.compatibility_service.get_item_detail_cached",
                new=AsyncMock(
                    return_value={
                        "category_id": "MLC1748",
                        "user_product_id": "MLCU123",
                    }
                ),
            ),
            patch(
                "services.compatibility_service.call_ml",
                new=AsyncMock(return_value=201),
            ),
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

    async def test_resolution_requires_all_six_vehicle_values(self):
        incomplete_row = {
            **self.row,
            "TRANSMISION": "",
        }
        contextual_resolver = AsyncMock()

        with patch(
            "services.compatibility_service.get_item_detail_cached",
            new=AsyncMock(
                return_value={
                    "category_id": "MLC1748",
                    "user_product_id": "MLCU123",
                }
            ),
        ) as get_item:
            result = await resolve_vehicle_product_row(
                access_token="token",
                user_id="123",
                row=incomplete_row,
                catalog_cache=SimpleNamespace(
                    resolve_vehicle_attribute_ids=contextual_resolver
                ),
                caches=JobCaches(),
                metrics=JobMetrics(),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error_code"],
            "MISSING_REQUIRED_VEHICLE_DATA",
        )
        self.assertIn("TRANSMISION", result["reason"])
        get_item.assert_awaited_once()
        contextual_resolver.assert_not_awaited()

    def test_same_vehicle_on_two_items_validates_both_mlcs(self):
        second_item_row = {
            **self.row,
            "ASOCIACION ML": "MLC999",
        }

        unique_entries, _indices = build_vehicle_resolution_plan(
            [self.row, second_item_row]
        )

        self.assertEqual(len(unique_entries), 2)


class ContextualCatalogResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_top_values_are_resolved_progressively(self):
        values_by_attribute = {
            "BRAND": [{"id": "100", "name": "Chevrolet"}],
            "CAR_AND_VAN_MODEL": [{"id": "200", "name": "Sail"}],
            "YEAR": [{"id": "300", "name": "2018"}],
            "CAR_AND_VAN_SUBMODEL": [{"id": "400", "name": "LT"}],
            "CAR_AND_VAN_ENGINE": [{"id": "500", "name": "1.5"}],
            "TRANSMISSION_CONTROL_TYPE": [
                {"id": "600", "name": "Manual"}
            ],
        }
        known_by_attribute = {}

        async def fake_call_ml(_fn, _token, attribute_id, **kwargs):
            known_by_attribute[attribute_id] = kwargs.get("known_attributes")
            return values_by_attribute[attribute_id]

        catalog = CatalogPreloadService(
            call_ml=fake_call_ml,
            metrics=JobMetrics(),
        )
        await catalog.preload_all("token", user_id="123")
        resolved = await catalog.resolve_vehicle_attribute_ids(
            access_token="token",
            user_id="123",
            brand_name="Chevrolet",
            model_name="Sail",
            year=2018,
            version_name="LT",
            engine_name="1.5",
            transmission_name="Manual",
        )

        self.assertEqual(
            resolved,
            {
                "brand_id": "100",
                "model_id": "200",
                "year_id": "300",
                "version_id": "400",
                "engine_id": "500",
                "transmission_id": "600",
            },
        )
        self.assertEqual(
            known_by_attribute["CAR_AND_VAN_MODEL"],
            [{"id": "BRAND", "value_id": "100"}],
        )
        self.assertEqual(
            known_by_attribute["TRANSMISSION_CONTROL_TYPE"],
            [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_id": "300"},
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
            ],
        )


class CompatibilityEndToEndOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_gets_item_before_counting_and_posting_family(self):
        events = []
        attributes = [
            {"id": "BRAND", "value_id": "100"},
            {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
            {"id": "YEAR", "value_id": "300"},
            {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
            {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
            {"id": "TRANSMISSION_CONTROL_TYPE", "value_id": "600"},
        ]

        async def get_item(**_kwargs):
            events.append("get_item")
            return {
                "category_id": "MLC1748",
                "user_product_id": "MLCU123",
            }

        async def resolve_attributes(**_kwargs):
            events.append("resolve_attributes")
            return {
                "brand_id": "100",
                "model_id": "200",
                "year_id": "300",
                "version_id": "400",
                "engine_id": "500",
                "transmission_id": "600",
            }

        async def count_family(*_args, **_kwargs):
            events.append("count_family")
            return 1

        async def post_family(*_args, **kwargs):
            events.append("post_family")
            self.assertEqual(
                kwargs["product_families"][0]["attributes"],
                attributes,
            )
            return {"created_compatibilities_count": 1}

        catalog_data = SimpleNamespace(stats=lambda: {"brands": 1})
        catalog_cache = SimpleNamespace(
            preload_all=AsyncMock(return_value=catalog_data),
            resolve_vehicle_attribute_ids=resolve_attributes,
        )
        row = {
            "ASOCIACION ML": "MLC123",
            "MARCA": "Chevrolet",
            "MODELO": "Sail",
            "VERSION": "LT",
            "CILINDRADA": "1.5",
            "TRANSMISION": "Manual",
            "AÑO": 2018,
            "FAMILIA": "",
            "POSICION_DT": "",
            "POSICION_ID": "",
        }

        with (
            patch(
                "services.compatibility_orchestrator_service.CatalogPreloadService",
                return_value=catalog_cache,
            ),
            patch(
                "services.compatibility_service.get_item_detail_cached",
                new=get_item,
            ),
            patch(
                "services.compatibility_service.call_ml",
                new=count_family,
            ),
            patch(
                "services.compatibility_batch_service.call_ml",
                new=post_family,
            ),
            patch(
                "services.compatibility_orchestrator_service.JobStore.update"
            ),
        ):
            outcome = await process_excel_compatibilities_end_to_end(
                job_id="job-flow",
                access_token="token",
                user_id="123",
                rows=[row],
            )

        self.assertEqual(
            events,
            [
                "get_item",
                "resolve_attributes",
                "count_family",
                "post_family",
            ],
        )
        self.assertEqual(
            outcome["summary"]["total_created_compatibilities"],
            1,
        )


class CompatibilityFamilyBatchTests(unittest.IsolatedAsyncioTestCase):
    def test_incomplete_resolved_family_is_rejected_before_write(self):
        row = _family_row()
        row["product_family"]["attributes"] = row["product_family"][
            "attributes"
        ][:3]

        validated = validate_resolved_family_rows([row])

        self.assertFalse(validated[0]["ok"])
        self.assertEqual(
            validated[0]["error_code"],
            "INVALID_PRODUCT_FAMILY_ATTRIBUTES",
        )
        self.assertIn("CAR_AND_VAN_ENGINE", validated[0]["error_message"])
        self.assertIn(
            "TRANSMISSION_CONTROL_TYPE",
            validated[0]["error_message"],
        )

    def test_resolved_family_with_extra_attribute_is_rejected(self):
        row = _family_row()
        row["product_family"]["attributes"].append(
            {"id": "EXTRA_ATTRIBUTE", "value_id": "700"}
        )

        validated = validate_resolved_family_rows([row])

        self.assertFalse(validated[0]["ok"])
        self.assertEqual(
            validated[0]["error_code"],
            "INVALID_PRODUCT_FAMILY_ATTRIBUTES",
        )
        self.assertIn("EXTRA_ATTRIBUTE", validated[0]["error_message"])

    async def test_incomplete_resolved_family_never_reaches_ml(self):
        row = _family_row()
        row["product_family"]["attributes"] = row["product_family"][
            "attributes"
        ][:3]

        with patch(
            "services.compatibility_batch_service.call_ml",
            new=AsyncMock(),
        ) as call_ml:
            outcome = await process_compatibility_batches(
                access_token="token",
                user_id="123",
                rows=[row],
            )

        call_ml.assert_not_awaited()
        self.assertFalse(outcome["results"][0]["ok"])
        self.assertEqual(
            outcome["results"][0]["error_code"],
            "INVALID_PRODUCT_FAMILY_ATTRIBUTES",
        )

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

    def test_failed_items_are_exposed_as_partial_process_errors(self):
        summary = build_compat_summary(
            [
                {
                    "ok": False,
                    "item_id": "MLC123",
                    "error_type": "functional",
                }
            ],
            [],
            JobMetrics(),
        )

        self.assertEqual(summary["failed_item_ids"], ["MLC123"])
        self.assertTrue(
            _has_partial_process_errors("compatibilities", summary)
        )


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
