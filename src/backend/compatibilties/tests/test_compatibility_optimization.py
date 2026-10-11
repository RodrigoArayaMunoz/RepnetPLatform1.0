"""Exercise optimization against a stateful API double, including failed writes."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from config import settings
from services.compatibility_batch_service import DEFAULT_COMPATIBILITY_NOTE
from services.compatibility_copy_service import _products
from services.compatibility_family_cache import get_family_resolution, save_family_resolution
from services.compatibility_orchestrator_service import (
    plan_complete_item_chunks, process_excel_compatibilities_end_to_end,
)
from services.ml_client import MercadoLibreClient, ml_client


def excel_row(item="MLC1", *, year=2013, position="DELANTERA", side="IZQUIERDA/DERECHA"):
    return {"ASOCIACION ML": item, "MARCA": "Kia", "MODELO": "Cerato", "AÑO": year,
            "VERSION": "EX", "CILINDRADA": "1.6L L4 Gasolina", "TRANSMISION": "Manual",
            "FAMILIA": "MLC-VEHICLE_SHOCK_ABSORBERS",
            "DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE": position, "IZQUIERDA/DERECHA": side}


class StatefulCompatibilityAPI:
    def __init__(self):
        self.calls = []
        self.listings = {}
        self.count = 2
        self.name_failure: int | Exception | None = None
        self.copy_partial = False
        self.fail_updates = False
        self.source_changes: Callable[[dict], None] | None = None

    @staticmethod
    def product_ids(attributes, count):
        key = hashlib.sha256(json.dumps(attributes, sort_keys=True).encode()).hexdigest()[:12]
        return [key + ":" + str(index) for index in range(count)]

    async def request(self, method, path, **kwargs):
        body = kwargs.get("json_body", {})
        self.calls.append({"method": method, "path": path, "body": copy.deepcopy(body)})
        await asyncio.sleep(0)
        if path.startswith("/items/"):
            item = path.rsplit("/", 1)[-1]
            return {"id": item, "category_id": "MLC22579", "user_product_id": "MLCU" + item[3:]}
        if path.endswith("/count_family_products"):
            if self.name_failure is not None and "value_name" in body["attributes"][0]:
                if isinstance(self.name_failure, Exception):
                    raise self.name_failure
                return {"count": self.name_failure}
            return {"count": self.count}
        assert path.startswith("/user-products/"), path
        up = path.split("/")[2]
        products = self.listings.setdefault(up, {})
        if method == "GET":
            if self.source_changes is not None and up == "MLCU1" and products:
                self.source_changes(products)
            return {"products": copy.deepcopy(list(products.values()))}
        if path.endswith("/copy-paste"):
            source = self.listings[body["user_product_id"]]
            for index, (product_id, product) in enumerate(source.items()):
                if self.copy_partial and index:
                    break
                products[product_id] = {**copy.deepcopy(product), "user_product_id": up}
            return {"created_compatibilities_count": len(products)}
        families = body["products_families"] if method == "POST" else body["update"]["products_families"]
        if method == "PUT" and self.fail_updates:
            raise HTTPException(status_code=503, detail="Update unavailable")
        created = 0
        for family in families:
            for product_id in self.product_ids(family["attributes"], self.count):
                if method == "POST" and product_id in products:
                    continue  # Creation does not change existing metadata.
                if method == "PUT" and product_id not in products:
                    continue
                created += product_id not in products
                products[product_id] = {"catalog_product_id": product_id, "source": "SELLER",
                                        "note": family.get("note", ""),
                                        "restrictions": copy.deepcopy(family.get("restrictions", [])),
                                        "user_product_id": up}
        return {"created_compatibilities_count": created} if method == "POST" else {"update": {}}

    def writes(self):
        return [call for call in self.calls if call["path"].startswith("/user-products/") and call["method"] != "GET"]

    def count_calls(self):
        return [call for call in self.calls if call["path"].endswith("/count_family_products")]


class CompatibilityOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "upload_dir", temporary))
        self.enterContext(patch.object(settings, "compatibility_family_cache_ttl_seconds", 3600))
        self.enterContext(patch.object(settings, "compatibility_use_value_names", True))
        self.enterContext(patch.object(settings, "compatibility_enable_copy", False))
        self.enterContext(patch.object(settings, "compatibility_chunk_pause_seconds", 0))
        self.enterContext(patch("services.compatibility_orchestrator_service.JobStore.update"))
        self.catalog = SimpleNamespace(preload_all=AsyncMock(), resolve_vehicle_attribute_ids=AsyncMock(return_value={
            "brand_id": "1", "model_id": "2", "year_id": "3", "version_id": "4",
            "engine_id": "5", "transmission_id": "6",
        }))
        self.enterContext(patch("services.compatibility_orchestrator_service.CatalogPreloadService", return_value=self.catalog))
        self.api = StatefulCompatibilityAPI()
        self.enterContext(patch.object(ml_client, "request", new=AsyncMock(side_effect=self.api.request)))

    async def run_rows(self, rows):
        return await process_excel_compatibilities_end_to_end(job_id="test", access_token="token", user_id="123", rows=rows)

    def assert_success(self, outcome):
        self.assertTrue(all(row["ok"] for row in outcome["results"]), outcome["results"])

    async def test_names_share_one_count_across_different_targets_under_concurrency(self):
        outcome = await self.run_rows([excel_row("MLC" + str(index)) for index in range(1, 5)])
        self.assert_success(outcome)
        self.assertEqual(len(self.api.count_calls()), 1)
        self.assertEqual(len(self.api.writes()), 4)
        self.assertTrue(all(call["method"] == "POST" for call in self.api.writes()))
        self.assertEqual({row["user_product_id"] for row in outcome["results"]}, {"MLCU1", "MLCU2", "MLCU3", "MLCU4"})
        for write in self.api.writes():
            family = write["body"]["products_families"][0]
            self.assertTrue(all("value_name" in attribute and "value_id" not in attribute for attribute in family["attributes"]))
            self.assertEqual(family["note"], DEFAULT_COMPATIBILITY_NOTE)
            self.assertEqual(len(family["restrictions"][0]["attribute_values"]), 2)
        self.catalog.preload_all.assert_not_awaited()
        self.catalog.resolve_vehicle_attribute_ids.assert_not_awaited()

    async def test_persistent_counts_and_verified_resume_across_jobs(self):
        self.assert_success(await self.run_rows([excel_row()]))
        self.api.calls.clear()
        outcome = await self.run_rows([excel_row(), excel_row("MLC2")])
        self.assert_success(outcome)
        self.assertEqual(self.api.count_calls(), [])
        self.assertEqual([write["path"] for write in self.api.writes()], ["/user-products/MLCU2/compatibilities"])
        self.assertEqual(outcome["batch_results"][0]["write_strategy"], "already_applied")

    async def test_changed_note_invalidates_completion_and_updates_existing_products(self):
        await self.run_rows([excel_row()])
        next(iter(self.api.listings["MLCU1"].values()))["note"] = "Changed by seller"
        self.api.calls.clear()
        self.assert_success(await self.run_rows([excel_row()]))
        self.assertEqual([write["method"] for write in self.api.writes()], ["POST", "PUT"])
        self.assertTrue(all(product["note"] == DEFAULT_COMPATIBILITY_NOTE for product in self.api.listings["MLCU1"].values()))

    async def test_removed_product_invalidates_completion_and_repairs_mixed_family(self):
        await self.run_rows([excel_row()])
        self.api.listings["MLCU1"].pop(next(iter(self.api.listings["MLCU1"])))
        self.api.calls.clear()
        outcome = await self.run_rows([excel_row()])
        self.assert_success(outcome)
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 1)
        self.assertEqual([write["method"] for write in self.api.writes()], ["POST", "PUT"])
        self.assertEqual(len(self.api.listings["MLCU1"]), 2)

    async def test_failed_put_does_not_create_completion_checkpoint(self):
        await self.run_rows([excel_row()])
        self.api.listings["MLCU1"].pop(next(iter(self.api.listings["MLCU1"])))
        next(iter(self.api.listings["MLCU1"].values()))["note"] = "Old note"
        self.api.fail_updates = True
        failed = await self.run_rows([excel_row()])
        self.assertFalse(failed["results"][0]["ok"])
        self.assertEqual(failed["summary"]["total_created_compatibilities"], 1)
        self.api.fail_updates = False
        self.api.calls.clear()
        self.assert_success(await self.run_rows([excel_row()]))
        self.assertEqual([write["method"] for write in self.api.writes()], ["POST", "PUT"])

    async def test_unknown_names_use_contextual_ids_and_cache_the_fallback(self):
        self.api.name_failure = 0
        self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.assertEqual(len(self.api.count_calls()), 2)
        self.catalog.resolve_vehicle_attribute_ids.assert_awaited_once()
        self.assertTrue(all("value_id" in attr for attr in self.api.writes()[0]["body"]["products_families"][0]["attributes"]))

    async def test_invalid_name_http400_can_fall_back_to_ids(self):
        self.api.name_failure = HTTPException(status_code=400, detail="Invalid attributes")
        self.assert_success(await self.run_rows([excel_row()]))
        self.catalog.resolve_vehicle_attribute_ids.assert_awaited_once()

    async def test_transient_count_failure_does_not_use_ids_or_write(self):
        self.api.name_failure = HTTPException(status_code=503, detail="Unavailable")
        outcome = await self.run_rows([excel_row()])
        self.assertFalse(outcome["results"][0]["ok"])
        self.catalog.resolve_vehicle_attribute_ids.assert_not_awaited()
        self.assertEqual(self.api.writes(), [])

    async def test_ambiguous_family_over200_is_rejected_before_write(self):
        self.api.count = 201
        outcome = await self.run_rows([excel_row()])
        self.assertFalse(outcome["results"][0]["ok"])
        self.assertEqual(self.api.writes(), [])

    async def test_missing_contextual_id_or_zero_count_never_writes(self):
        self.api.name_failure = 0
        self.catalog.resolve_vehicle_attribute_ids.return_value["version_id"] = None
        failed = await self.run_rows([excel_row()])
        self.assertFalse(failed["results"][0]["ok"])
        self.assertEqual(self.api.writes(), [])
        self.catalog.resolve_vehicle_attribute_ids.return_value["version_id"] = "4"
        self.api.count = 0
        failed = await self.run_rows([excel_row()])
        self.assertFalse(failed["results"][0]["ok"])
        self.assertEqual(self.api.writes(), [])

    async def test_item_grouping_merges_positions_and_preserves_interleaved_excel_order(self):
        self.enterContext(patch.object(settings, "compatibility_chunk_size", 1))
        rows = [excel_row(side="IZQUIERDA"), excel_row("MLC2", position="", side="DERECHA"), excel_row(side="DERECHA")]
        outcome = await self.run_rows(rows)
        self.assert_success(outcome)
        self.assertEqual([row["original_row_index"] for row in outcome["results"]], [0, 1, 2])
        self.assertEqual([row["item_id"] for row in outcome["results"]], ["MLC1", "MLC2", "MLC1"])
        self.assertEqual(len(self.api.writes()), 2)
        self.assertTrue(all(write["method"] == "POST" for write in self.api.writes()))
        front = next(write for write in self.api.writes() if "/MLCU1/" in write["path"])
        self.assertEqual(len(front["body"]["products_families"][0]["restrictions"][0]["attribute_values"]), 2)

    async def test_batches_keep_max10_families_and200_matched_products(self):
        self.api.count = 30
        outcome = await self.run_rows([excel_row(year=2000 + index) for index in range(11)])
        self.assert_success(outcome)
        self.assertEqual([len(write["body"]["products_families"]) for write in self.api.writes()], [6, 5])
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 330)

    async def test_verified_copy_includes_metadata_and_repeats_without_writes(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        rows = [excel_row(), excel_row("MLC2")]
        outcome = await self.run_rows(rows)
        self.assert_success(outcome)
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 4)
        self.assertEqual([write["path"].rsplit("/", 1)[-1] for write in self.api.writes()], ["compatibilities", "copy-paste"])
        self.assertEqual(outcome["batch_results"][1]["write_strategy"], "copy")
        self.api.calls.clear()
        self.assert_success(await self.run_rows(rows))
        self.assertEqual(self.api.writes(), [])

    async def test_partial_copy_is_completed_by_normal_family_post_and_put(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        self.api.copy_partial = True
        outcome = await self.run_rows([excel_row(), excel_row("MLC2")])
        self.assert_success(outcome)
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 4)
        self.assertEqual([write["method"] for write in self.api.writes()], ["POST", "POST", "POST", "PUT"])
        self.assertEqual(len(self.api.listings["MLCU2"]), 2)

    async def test_source_extra_vehicle_prevents_copy_to_other_listing(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        self.api.source_changes = lambda products: products.update({"unrelated": {"catalog_product_id": "unrelated", "source": "SELLER", "note": "Other"}})
        self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.assertFalse(any(write["path"].endswith("copy-paste") for write in self.api.writes()))
        self.assertNotIn("unrelated", self.api.listings["MLCU2"])

    async def test_copy_source_with_claims_is_excluded(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        self.api.source_changes = lambda products: next(iter(products.values())).update({"reputation": {"level": "RED", "total": 1}})
        self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.assertFalse(any(write["path"].endswith("copy-paste") for write in self.api.writes()))

    async def test_source_metadata_changes_after_registration_prevent_copy(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        reads = 0
        def change_on_second_read(products):
            nonlocal reads
            reads += 1
            if reads >= 2:
                next(iter(products.values()))["note"] = "Changed source"
        self.api.source_changes = change_on_second_read
        self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.assertFalse(any(write["path"].endswith("copy-paste") for write in self.api.writes()))
        self.assertTrue(all(product["note"] == DEFAULT_COMPATIBILITY_NOTE for product in self.api.listings["MLCU2"].values()))

    async def test_copy_source_can_be_reused_across_chunks_and_jobs(self):
        self.enterContext(patch.object(settings, "compatibility_enable_copy", True))
        self.enterContext(patch.object(settings, "compatibility_chunk_size", 1))
        self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.api.calls.clear()
        self.assert_success(await self.run_rows([excel_row("MLC3"), excel_row("MLC4")]))
        self.assertTrue(all(write["path"].endswith("copy-paste") for write in self.api.writes()))
        self.assertEqual(len(self.api.writes()), 2)

    async def test_catalog_summary_does_not_allow_a_checkpoint_to_skip_repair(self):
        await self.run_rows([excel_row()])
        self.api.listings["MLCU1"]["catalog"] = {"catalog_product_id": None, "source": "CATALOGO", "total": 200}
        self.api.calls.clear()
        self.assert_success(await self.run_rows([excel_row()]))
        self.assertEqual([write["method"] for write in self.api.writes()], ["POST", "PUT"])

    async def test_sqlite_cache_expires_and_ttl_zero_disables_it(self):
        with patch("services.compatibility_family_cache.time.time", return_value=100):
            await save_family_resolution("example", {"count": 2})
        with patch("services.compatibility_family_cache.time.time", return_value=101):
            self.assertEqual(await get_family_resolution("example"), {"count": 2})
        with patch("services.compatibility_family_cache.time.time", return_value=3701):
            self.assertIsNone(await get_family_resolution("example"))
        with patch.object(settings, "compatibility_family_cache_ttl_seconds", 0):
            await save_family_resolution("disabled", {"count": 2})
        self.assertIsNone(await get_family_resolution("disabled"))

    async def test_unavailable_sqlite_uses_job_cache_and_completes_normal_writes(self):
        with patch("services.compatibility_family_cache.sqlite3.connect", side_effect=OSError("Unavailable")):
            self.assert_success(await self.run_rows([excel_row(), excel_row("MLC2")]))
        self.assertEqual(len(self.api.count_calls()), 1)
        self.assertEqual(len(self.api.writes()), 2)


class CopyContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_exclusive_source_up_and_extended_information(self):
        client = MercadoLibreClient()
        with patch.object(client, "request", new=AsyncMock(return_value={})) as request:
            await client.copy_user_product_compatibilities(access_token="token", user_product_id="TARGET",
                                                           source_user_product_id="SOURCE", category_id="MLC22579")
        call = request.await_args
        assert call is not None
        self.assertEqual(call.args, ("POST", "/user-products/TARGET/compatibilities/copy-paste"))
        self.assertEqual(call.kwargs["json_body"], {"domain_id": settings.ml_domain_id, "category_id": "MLC22579",
                                                   "user_product_id": "SOURCE", "item_id": None, "extended_information": True})

    def test_incomplete_catalog_or_duplicate_lists_cannot_be_used_for_copy(self):
        product = {"catalog_product_id": "MLC1", "source": "SELLER"}
        for response in [{"products": [product], "paging": {"total": 2}},
                         {"products": [product], "total": 2},
                         {"products": [product], "catalog_compatibilities_count": 3},
                         {"products": [product, product]}, {"products": [None]},
                         {"products": [{"catalog_product_id": None, "source": "CATALOGO"}]}]:
            with self.subTest(response=response):
                self.assertIsNone(_products(response))

    def test_chunk_plan_keeps_whole_listing_and_marks_only_equal_complete_sets(self):
        rows = [excel_row(), excel_row("MLC2", position="TRASERA"), excel_row(), excel_row("MLC3")]
        chunks, candidates = plan_complete_item_chunks(rows, 1)
        self.assertEqual([[index for index, _ in chunk] for chunk in chunks], [[0, 2], [1], [3]])
        self.assertEqual(candidates, {"MLC1", "MLC3"})
