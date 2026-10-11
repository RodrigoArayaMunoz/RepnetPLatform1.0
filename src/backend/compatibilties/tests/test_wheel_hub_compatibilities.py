import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
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


WHEEL_HUBS = "MLC-VEHICLE_WHEEL_HUBS"
POSITION_IDS = {
    "Delantera": "13701104",
    "Trasera": "13701105",
    "Conductor": "13373175",
    "Acompañante": "13373176",
    "Izquierda": "2262158",
    "Derecha": "2262160",
}


def _combinations(restrictions):
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def _resolved_row(position="Delantera", side="", item_id="MLC123"):
    return {
        "ok": True,
        "item_id": item_id,
        "category_id": "MLC161586",
        "user_product_id": "MLCU123",
        "year": 2005,
        "family_product_count": 1,
        "product_family": {
            "domain_id": settings.ml_domain_id,
            "creation_source": "DEFAULT",
            "attributes": [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_id": "300"},
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
                {"id": "TRANSMISSION_CONTROL_TYPE", "value_id": "600"},
            ],
        },
        "familia": WHEEL_HUBS,
        "posicion_dt": position,
        "posicion_id": side,
    }


def _write_response(method, _path, **kwargs):
    body = kwargs["json_body"]
    if method == "POST":
        return {"created_compatibilities_count": len(body["products_families"])}
    return {"update": {}}


class WheelHubPositionTests(unittest.TestCase):
    def test_all_four_positions_use_verified_ids_and_optional_sides(self):
        side_cases = [
            (None, [None]),
            ("", [None]),
            ("   ", [None]),
            ("IZQUIERDA", ["Izquierda"]),
            ("DERECHA", ["Derecha"]),
            ("IZQUIERDA/DERECHA", ["Izquierda", "Derecha"]),
            (" izquierda / derecha ", ["Izquierda", "Derecha"]),
        ]
        for position in ("Delantera", "Trasera", "Conductor", "Acompañante"):
            for side, expected_sides in side_cases:
                with self.subTest(position=position, side=side):
                    restrictions = build_restrictions(
                        " mlc-vehicle_wheel_hubs ", position.upper(), side,
                    )
                    self.assertEqual(restrictions[0]["attribute_id"], "POSITION")
                    expected = {
                        frozenset([POSITION_IDS[position]] + ([POSITION_IDS[value]] if value else []))
                        for value in expected_sides
                    }
                    self.assertEqual(_combinations(restrictions), expected)
                    for combination in restrictions[0]["attribute_values"]:
                        for value in combination["values"]:
                            self.assertEqual(value["value_id"], POSITION_IDS[value["value_name"]])

    def test_opposite_sides_are_separate_alternatives(self):
        restrictions = build_restrictions(WHEEL_HUBS, "DELANTERA", "IZQUIERDA/DERECHA")
        self.assertEqual(_combinations(restrictions), {
            frozenset({"13701104", "2262158"}),
            frozenset({"13701104", "2262160"}),
        })
        self.assertEqual(len(restrictions[0]["attribute_values"]), 2)

    def test_existing_family_rules_remain_unchanged(self):
        cases = [
            ("MLC-VEHICLE_BRAKE_PADS", "", {
                frozenset({"13701104", "2262158"}),
                frozenset({"13701104", "2262160"}),
            }),
            ("MLC-VEHICLE_HEADLIGHTS", "IZQUIERDA", {frozenset({"13701104", "2262158"})}),
            ("MLC-VEHICLE_WHEELS_BEARINGS", "IZQUIERDA/DERECHA", {frozenset({"13701104"})}),
            ("UNKNOWN", "IZQUIERDA", set()),
        ]
        for family, side, expected in cases:
            with self.subTest(family=family):
                self.assertEqual(_combinations(build_restrictions(family, "Delantera", side)), expected)

    def test_same_vehicle_merges_positions_without_duplicate_combinations(self):
        rows = [
            _resolved_row(side="Izquierda"),
            _resolved_row(side="Derecha"),
            _resolved_row(side="IZQUIERDA/DERECHA"),
            _resolved_row(position="Trasera", side="Derecha"),
        ]
        before = copy.deepcopy(rows)
        grouped = build_grouped_product_families(rows)
        self.assertEqual(len(grouped["MLC123"]), 1)
        restrictions = grouped["MLC123"][0]["payload"]["restrictions"]
        self.assertEqual(_combinations(restrictions), {
            frozenset({"13701104", "2262158"}),
            frozenset({"13701104", "2262160"}),
            frozenset({"13701105", "2262160"}),
        })
        self.assertEqual(len(restrictions[0]["attribute_values"]), 3)
        self.assertEqual(rows, before)


class WheelHubBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_position_reports_row_error_without_ml_write(self):
        for position, side in (("", ""), ("DELANTERO", ""), ("Delantera", "AMBAS"), ("Delantera", "IZQUIERDA/")):
            with self.subTest(position=position, side=side):
                with patch.object(ml_client, "request", new=AsyncMock()) as request:
                    outcome = await process_compatibility_batches(
                        access_token="token", user_id="123",
                        rows=[_resolved_row(position, side)],
                    )
                request.assert_not_awaited()
                self.assertFalse(outcome["results"][0]["ok"])
                self.assertEqual(outcome["results"][0]["error_code"], "INVALID_WHEEL_HUB_POSITION")

    async def test_invalid_row_does_not_block_a_valid_row_of_the_same_vehicle(self):
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123",
                rows=[_resolved_row(side="AMBAS"), _resolved_row(side="Izquierda")],
            )
        self.assertEqual(request.await_count, 2)
        self.assertEqual([row["ok"] for row in outcome["results"]], [False, True])
        self.assertEqual(_combinations(request.await_args_list[0].kwargs["json_body"]["products_families"][0]["restrictions"]), {
            frozenset({"13701104", "2262158"}),
        })

    async def test_positions_are_preserved_across_chunks_and_repeated_rows_are_reused(self):
        state = CompatibilityBatchState()
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            first = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_resolved_row(side="Izquierda")],
            )
            second = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_resolved_row(side="Derecha")],
            )
            repeated = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_resolved_row(side="Izquierda")],
            )
        self.assertTrue(first["results"][0]["ok"])
        self.assertTrue(second["results"][0]["ok"])
        self.assertTrue(repeated["results"][0]["was_reused"])
        self.assertEqual(request.await_count, 4)
        for call in request.await_args_list[2:]:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            self.assertEqual(_combinations(families[0]["restrictions"]), {
                frozenset({"13701104", "2262158"}),
                frozenset({"13701104", "2262160"}),
            })

    async def test_failed_update_is_not_remembered_as_applied_positions(self):
        state = CompatibilityBatchState()

        def fail_update(method, path, **kwargs):
            if method == "PUT":
                raise HTTPException(status_code=400, detail="Invalid position")
            return _write_response(method, path, **kwargs)

        with patch.object(ml_client, "request", new=AsyncMock(side_effect=fail_update)):
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_resolved_row(side="Izquierda")],
            )
        self.assertFalse(outcome["results"][0]["ok"])
        self.assertEqual(state.position_restrictions, {})
        self.assertEqual(state.successful_rows, {})


class WheelHubExcelFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_supplied_excel_rows_reach_creation_and_update_with_correct_positions(self):
        fixture_path = Path(__file__).parent / "fixtures" / "wheel_hubs_excel_rows.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wheel-hubs.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Hoja1"
            worksheet.append(fixture["headers"])
            for cells in fixture["rows"]:
                worksheet.append(cells)
            workbook.save(path)
            workbook.close()
            rows = load_excel_rows(str(path))

        self.assertEqual(len(rows), 6)
        self.assertEqual([row["POSICION_ID"] for row in rows], [None] * 3 + ["IZQUIERDA/DERECHA"] * 3)
        item_targets = {"MLC4557186208": "MLCU100", "MLC4557224882": "MLCU200"}

        async def resolve_attributes(**kwargs):
            return {
                "brand_id": "brand:" + kwargs["brand_name"],
                "model_id": "model:" + kwargs["model_name"],
                "year_id": str(kwargs["year"]),
                "version_id": "version:" + kwargs["version_name"],
                "engine_id": "engine:" + kwargs["engine_name"],
                "transmission_id": "transmission:" + kwargs["transmission_name"],
            }

        def request_response(method, path, **kwargs):
            if method == "GET" and path.startswith("/items/"):
                return {"category_id": "MLC161586", "user_product_id": item_targets[path.rsplit("/", 1)[-1]]}
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
                job_id="wheel-hubs-test", access_token="token", user_id="123", rows=rows,
            )

        self.assertEqual(len(outcome["results"]), 6)
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 6)
        writes = [call for call in request.await_args_list if call.args[1].startswith("/user-products/")]
        self.assertEqual(len(writes), 4)
        for call in writes:
            method, target = call.args
            body = call.kwargs["json_body"]
            families = body["products_families"] if method == "POST" else body["update"]["products_families"]
            self.assertEqual(len(families), 3)
            expected = {frozenset({"13701104"})} if target.startswith("/user-products/MLCU100/") else {
                frozenset({"13701104", "2262158"}),
                frozenset({"13701104", "2262160"}),
            }
            for family in families:
                self.assertEqual(_combinations(family["restrictions"]), expected)
                self.assertEqual(len(family["restrictions"][0]["attribute_values"]), len(expected))
                self.assertEqual(len(family["attributes"]), 6)
                self.assertTrue(family["note"])


if __name__ == "__main__":
    unittest.main()
