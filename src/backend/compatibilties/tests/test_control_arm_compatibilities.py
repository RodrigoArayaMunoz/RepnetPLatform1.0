import copy
import json
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


CONTROL_ARMS = "MLC-VEHICLE_SUSPENSION_CONTROL_ARMS"


def _combinations(restrictions):
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def _control_arm_row(position="Delantera", side="Izquierda", item_id="MLC123"):
    return {
        "ok": True,
        "item_id": item_id,
        "category_id": "MLC161582",
        "user_product_id": "MLCU" + item_id[3:],
        "year": 2010,
        "family_product_count": 1,
        "familia": CONTROL_ARMS,
        "posicion_dt": position,
        "posicion_id": side,
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
    }


def _write_response(method, _path, **kwargs):
    if method == "POST":
        return {"created_compatibilities_count": len(kwargs["json_body"]["products_families"])}
    return {"update": {}}


class ControlArmPositionTests(unittest.TestCase):
    def test_primary_positions_and_side_combinations_use_verified_ids(self):
        positions = {
            "Delantera": "13701104", "Trasera": "13701105",
            "Conductor": "13373175", "Acompañante": "13373176",
        }
        side_cases = {
            "": [None], "Izquierda": ["2262158"], "Derecha": ["2262160"],
            "IZQUIERDA/DERECHA": ["2262158", "2262160"],
        }
        for position, position_id in positions.items():
            for side, side_ids in side_cases.items():
                with self.subTest(position=position, side=side):
                    restrictions = build_restrictions(CONTROL_ARMS, position.upper(), side)
                    self.assertEqual(restrictions[0]["attribute_id"], "POSITION")
                    self.assertEqual(_combinations(restrictions), {
                        frozenset([position_id] + ([side_id] if side_id else []))
                        for side_id in side_ids
                    })

    def test_missing_primary_position_preserves_only_the_supplied_side(self):
        cases = [
            (None, "DERECHA", {frozenset({"2262160"})}),
            ("", "IZQUIERDA", {frozenset({"2262158"})}),
            ("  ", " izquierda / derecha ", {frozenset({"2262158"}), frozenset({"2262160"})}),
        ]
        for position, side, expected in cases:
            with self.subTest(position=position, side=side):
                restrictions = build_restrictions(" mlc-vehicle_suspension_control_arms ", position, side)
                self.assertEqual(_combinations(restrictions), expected)
                self.assertEqual(len(restrictions[0]["attribute_values"]), len(expected))

    def test_same_vehicle_preserves_distinct_row_positions(self):
        rows = [
            _control_arm_row(),
            _control_arm_row(side="Derecha"),
            _control_arm_row(side="IZQUIERDA/DERECHA"),
        ]
        before = copy.deepcopy(rows)
        entries = build_grouped_product_families(rows)["MLC123"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(_combinations(entries[0]["payload"]["restrictions"]), {
            frozenset({"13701104", "2262158"}),
            frozenset({"13701104", "2262160"}),
        })
        self.assertEqual(len(entries[0]["payload"]["restrictions"][0]["attribute_values"]), 2)
        self.assertEqual(rows, before)


class ControlArmBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_or_entirely_missing_positions_never_reach_ml(self):
        for position, side in ((None, None), ("DELANTERO", "Izquierda"), ("Delantera", "AMBAS"), (None, "IZQUIERDA/")):
            with self.subTest(position=position, side=side):
                with patch.object(ml_client, "request", new=AsyncMock()) as request:
                    outcome = await process_compatibility_batches(
                        access_token="token", user_id="123",
                        rows=[_control_arm_row(position, side)],
                    )
                request.assert_not_awaited()
                self.assertFalse(outcome["results"][0]["ok"])
                self.assertEqual(outcome["results"][0]["error_code"], "INVALID_CONTROL_ARM_POSITION")

    async def test_cross_chunk_positions_are_preserved_and_isolated_by_item(self):
        state = CompatibilityBatchState()
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            await process_compatibility_batches(
                access_token="token", user_id="123", state=state, rows=[_control_arm_row()],
            )
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123", state=state,
                rows=[_control_arm_row(side="Derecha"), _control_arm_row(side="Derecha", item_id="MLC456")],
            )
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        posts = [call for call in request.await_args_list[2:] if call.args[0] == "POST"]
        self.assertEqual(len(posts), 2)
        for call in posts:
            restrictions = call.kwargs["json_body"]["products_families"][0]["restrictions"]
            expected = {frozenset({"13701104", "2262160"})}
            if call.args[1] == "/user-products/MLCU123/compatibilities":
                expected.add(frozenset({"13701104", "2262158"}))
            self.assertEqual(_combinations(restrictions), expected)

    async def test_side_only_positions_reach_both_creation_and_update(self):
        with patch.object(ml_client, "request", new=AsyncMock(side_effect=_write_response)) as request:
            outcome = await process_compatibility_batches(
                access_token="token", user_id="123",
                rows=[_control_arm_row(None, "IZQUIERDA/DERECHA")],
            )
        self.assertTrue(outcome["results"][0]["ok"])
        self.assertEqual([call.args[0] for call in request.await_args_list], ["POST", "PUT"])
        for call in request.await_args_list:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            self.assertEqual(_combinations(families[0]["restrictions"]), {
                frozenset({"2262158"}), frozenset({"2262160"}),
            })


class ControlArmExcelFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_seven_position_patterns_from_the_supplied_excel_reach_post_and_put(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "control_arms_excel_rows.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control-arms.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Hoja1"
            worksheet.append(fixture["headers"])
            for cells in fixture["rows"]:
                worksheet.append(cells)
            workbook.save(path)
            workbook.close()
            rows = load_excel_rows(str(path))
        self.assertEqual(len(rows), 7)
        expected_patterns = [
            {frozenset({"13701104", "2262160"})},
            {frozenset({"13701104"})},
            {frozenset({"13701104", "2262158"})},
            {frozenset({"13701104", "2262158"}), frozenset({"13701104", "2262160"})},
            {frozenset({"2262160"})},
            {frozenset({"2262158"})},
            {frozenset({"2262158"}), frozenset({"2262160"})},
        ]
        expected_by_target = {
            "/user-products/MLCU" + row["ASOCIACION ML"][3:] + "/compatibilities": expected
            for row, expected in zip(rows, expected_patterns)
        }

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
                return {"category_id": "MLC161582", "user_product_id": "MLCU" + path.rsplit("/", 1)[-1][3:]}
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
                job_id="control-arms-test", access_token="token", user_id="123", rows=rows,
            )

        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        self.assertEqual(outcome["summary"]["total_created_compatibilities"], 7)
        writes = [call for call in request.await_args_list if call.args[1].startswith("/user-products/")]
        self.assertEqual(len(writes), 14)
        for call in writes:
            body = call.kwargs["json_body"]
            families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
            self.assertEqual(len(families), 1)
            self.assertEqual(_combinations(families[0]["restrictions"]), expected_by_target[call.args[1]])
            self.assertEqual(len(families[0]["restrictions"][0]["attribute_values"]), len(expected_by_target[call.args[1]]))
            self.assertEqual(body["category_id"], "MLC161582")


if __name__ == "__main__":
    unittest.main()
