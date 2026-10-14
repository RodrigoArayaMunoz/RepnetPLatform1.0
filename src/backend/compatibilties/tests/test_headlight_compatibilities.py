import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook

from config import settings
from services.compatibility_batch_service import (
    CompatibilityBatchState,
    build_grouped_product_families,
    build_restrictions,
    process_compatibility_batches,
)
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.excel_service import load_excel_rows
from services.ml_client import ml_client


HEADLIGHTS = "MLC-VEHICLE_HEADLIGHTS"
POSITION_IDS = {
    "Delantera": "13701104", "Trasera": "13701105",
    "Conductor": "13373175", "Acompañante": "13373176",
    "Izquierda": "2262158", "Derecha": "2262160",
}


def _combinations(restrictions):
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def _resolved_row(position: str | None = "Delantera", side: str | None = "Izquierda", item_id="MLC123"):
    return {
        "ok": True, "item_id": item_id,
        "category_id": "MLC161712", "user_product_id": "MLCU" + item_id[3:],
        "year": 2010, "family_product_count": 1,
        "familia": HEADLIGHTS, "posicion_dt": position, "posicion_id": side,
        "product_family": {
            "domain_id": settings.ml_domain_id, "creation_source": "DEFAULT",
            "attributes": [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_id": "300"},
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
                {"id": "TRANSMISSION_CONTROL_TYPE", "value_id": "600"},
            ],
        },
    }


def _write_response(method, _path, **kwargs):
    if method == "POST":
        return {"created_compatibilities_count": len(kwargs["json_body"]["products_families"])}
    return {"update": {}}


class _NoPersistentCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(settings, "compatibility_family_cache_ttl_seconds", 0))
        self.enterContext(patch.object(settings, "compatibility_enable_copy", False))


class HeadlightPositionTests(unittest.TestCase):
    def test_blank_primary_and_normalized_sides_use_only_supplied_values(self):
        cases = [
            (None, "DERECHA", {frozenset({"2262160"})}),
            ("", "IZQUIERDA", {frozenset({"2262158"})}),
            ("   ", " izquierda / derecha ", {frozenset({"2262158"}), frozenset({"2262160"})}),
            (" delantera ", " derecha / izquierda / derecha ", {
                frozenset({"13701104", "2262158"}), frozenset({"13701104", "2262160"}),
            }),
            (" acompañante ", "   ", {frozenset({"13373176"})}),
        ]
        for position, side, expected in cases:
            with self.subTest(position=position, side=side):
                restrictions = build_restrictions(" mlc-vehicle_headlights ", position, side)
                self.assertEqual(_combinations(restrictions), expected)
                self.assertEqual(len(restrictions[0]["attribute_values"]), len(expected))

    def test_same_vehicle_merges_all_row_positions_without_mutating_rows(self):
        rows = [
            _resolved_row(), _resolved_row(side="Derecha"),
            _resolved_row(side="IZQUIERDA/DERECHA"),
            _resolved_row(position="Trasera", side="Derecha"),
        ]
        before = copy.deepcopy(rows)
        entries = build_grouped_product_families(rows)["MLC123"]
        self.assertEqual(len(entries), 1)
        restrictions = entries[0]["payload"]["restrictions"]
        self.assertEqual(_combinations(restrictions), {
            frozenset({"13701104", "2262158"}),
            frozenset({"13701104", "2262160"}),
            frozenset({"13701105", "2262160"}),
        })
        self.assertEqual(len(restrictions[0]["attribute_values"]), 3)
        self.assertEqual(rows, before)


class HeadlightBatchTests(_NoPersistentCache):
    async def test_existing_family_put_preserves_note_and_side_only_alternatives(self):
        def response(method, _path, **_kwargs):
            return {"created_compatibilities_count": 0} if method == "POST" else {"update": {}}

        with patch.object(ml_client, "request", new=AsyncMock(side_effect=response)) as request:
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123", rows=[_resolved_row(position=None, side="IZQUIERDA/DERECHA")],
            )
        self.assertTrue(outcome["results"][0]["ok"])
        self.assertEqual([call.args[0] for call in request.await_args_list], ["POST", "PUT"])
        for call in request.await_args_list:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            self.assertTrue(families[0]["note"])
            self.assertEqual(_combinations(families[0]["restrictions"]), {frozenset({"2262158"}), frozenset({"2262160"})})

    async def test_invalid_or_entirely_missing_positions_report_errors_without_writes(self):
        cases = [(None, None), (" ", " "), ("DELANTERO", "Izquierda"),
                 ("Delantera", "AMBAS"), (None, "IZQUIERDA/")]
        for position, side in cases:
            with self.subTest(position=position, side=side):
                with patch.object(ml_client, "request", new=AsyncMock()) as request:
                    outcome = await process_compatibility_batches(
                        access_token="token", user_id="123", rows=[_resolved_row(position, side)],
                    )
                request.assert_not_awaited()
                self.assertFalse(outcome["results"][0]["ok"])
                self.assertEqual(outcome["results"][0]["error_code"], "INVALID_HEADLIGHT_POSITION")

    async def test_invalid_row_does_not_block_valid_row_for_the_same_vehicle(self):
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123",
                rows=[_resolved_row(side="AMBAS"), _resolved_row(side="Derecha")],
            )
        self.assertEqual([row["ok"] for row in outcome["results"]], [False, True])
        self.assertEqual(request.await_count, 1)
        for call in request.await_args_list:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            self.assertEqual(_combinations(families[0]["restrictions"]), {frozenset({"13701104", "2262160"})})

    async def test_positions_persist_across_chunks_and_stay_isolated_by_item(self):
        state = CompatibilityBatchState()
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            await process_compatibility_batches(
                access_token="token", user_id="123", state=state, rows=[_resolved_row()],
            )
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_resolved_row(side="Derecha"), _resolved_row(side="Derecha", item_id="MLC456")],
            )
            write_count = request.await_count
            repeated = await process_compatibility_batches(
                access_token="token", user_id="123", state=state, rows=[_resolved_row()],
            )
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertTrue(repeated["results"][0]["was_reused"])
        self.assertEqual(request.await_count, write_count)
        self.assertEqual(write_count, 3)
        for call in request.await_args_list[1:]:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            expected = {frozenset({"13701104", "2262160"})}
            if call.args[1] == "/user-products/MLCU123/compatibilities":
                expected.add(frozenset({"13701104", "2262158"}))
            self.assertEqual(_combinations(families[0]["restrictions"]), expected)


class HeadlightExcelFlowTests(_NoPersistentCache):
    async def test_all_nineteen_position_patterns_reach_single_post_from_excel_headers(self):
        side_cases = {
            "": [None], "IZQUIERDA": ["2262158"], "DERECHA": ["2262160"],
            "IZQUIERDA/DERECHA": ["2262158", "2262160"],
        }
        expected_by_target = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "headlights.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            assert worksheet is not None
            worksheet.title = "Hoja1"
            worksheet.append([
                "ASOCIACION ML", "MARCA", "MODELO", "AÑO", "VERSION", "CILINDRADA",
                "TRANSMISION", "FAMILIA", "DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE", "IZQUIERDA/DERECHA",
            ])
            for position in ("Delantera", "Trasera", "Conductor", "Acompañante", ""):
                for side, side_ids in side_cases.items():
                    if not position and not side:
                        continue
                    item_id = "MLC" + str(len(expected_by_target) + 1)
                    worksheet.append([
                        item_id, "Suzuki", "Alto", 2007, "GL 800", "0.8L L3 Gasolina", "Manual",
                        HEADLIGHTS, position.upper(), side,
                    ])
                    expected_by_target["/user-products/MLCU" + item_id[3:] + "/compatibilities"] = {
                        frozenset(([POSITION_IDS[position]] if position else []) + ([side_id] if side_id else []))
                        for side_id in side_ids
                    }
            workbook.save(path)
            workbook.close()
            rows = load_excel_rows(str(path))

        async def resolve_attributes(**kwargs):
            return {
                "brand_id": "100", "model_id": "200", "year_id": "300",
                "version_id": "400", "engine_id": "500", "transmission_id": "600",
            }

        def request_response(method, path, **kwargs):
            if method == "GET" and path.startswith("/items/"):
                return {"category_id": "MLC161712", "user_product_id": "MLCU" + path.rsplit("/", 1)[-1][3:]}
            if path == "/catalog_compatibilities/products_search/count_family_products":
                return {"count": 1}
            if path.startswith("/user-products/"):
                return _write_response(method, path, **kwargs)
            raise AssertionError(f"Unexpected ML request: {method} {path}")

        catalog = SimpleNamespace(
            preload_all=AsyncMock(return_value=SimpleNamespace(stats=lambda: {})),
            resolve_vehicle_attribute_ids=resolve_attributes,
        )
        with (
            patch("services.compatibility_orchestrator_service.CatalogPreloadService", return_value=catalog),
            patch("services.compatibility_orchestrator_service.JobStore.update"),
            patch.object(ml_client, "request", new=AsyncMock(side_effect=request_response)) as request,
        ):
            outcome = await process_excel_compatibilities_end_to_end(
                job_id="headlights-test", access_token="token", user_id="123", rows=rows,
            )
        self.assertEqual(len(outcome["results"]), 19)
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 19)
        writes = [call for call in request.await_args_list if call.args[0] in {"POST", "PUT"} and call.args[1].startswith("/user-products/")]
        self.assertEqual(len(writes), 19)
        for target, expected in expected_by_target.items():
            matching = [call for call in writes if call.args[1] == target]
            self.assertEqual({call.args[0] for call in matching}, {"POST"})
            for call in matching:
                body = call.kwargs["json_body"]
                families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
                self.assertEqual(len(families), 1)
                restrictions = families[0]["restrictions"]
                self.assertEqual(restrictions[0]["attribute_id"], "POSITION")
                self.assertEqual(_combinations(restrictions), expected)
                self.assertEqual(len(restrictions[0]["attribute_values"]), len(expected))
                for combination in restrictions[0]["attribute_values"]:
                    for value in combination["values"]:
                        self.assertEqual(value["value_id"], POSITION_IDS[value["value_name"]])
                self.assertEqual(body["category_id"], "MLC161712")


if __name__ == "__main__":
    unittest.main()
