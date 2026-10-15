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


POSITIONS = {
    "": None, "DELANTERA": "13701104", "TRASERA": "13701105",
    "CONDUCTOR": "13373175", "ACOMPAÑANTE": "13373176",
}
SIDES = {
    "": [None], "IZQUIERDA": ["2262158"], "DERECHA": ["2262160"],
    "IZQUIERDA/DERECHA": ["2262158", "2262160"],
}
NAMES = {
    "13701104": "Delantera", "13701105": "Trasera",
    "13373175": "Conductor", "13373176": "Acompañante",
    "2262158": "Izquierda", "2262160": "Derecha",
}
FAMILIES = (
    None, "", "   ", "BANDEJA SUSPENSION", "DESCONOCIDA",
    "MLC-VEHICLE_BRAKE_PADS", "MLC-VEHICLE_TAIL_LIGHTS",
    "MLC-VEHICLE_WHEELS_BEARINGS", "MLC-VEHICLE_WHEEL_HUBS",
    "MLC-VEHICLE_SUSPENSION_CONTROL_ARMS", "MLC-VEHICLE_SHOCK_ABSORBERS",
    "MLC-VEHICLE_HEADLIGHTS",
)


def combinations(restrictions):
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def expected(position, side):
    if not position and not side:
        return set()
    return {
        frozenset(([POSITIONS[position]] if position else []) + ([value] if value else []))
        for value in SIDES[side]
    }


def resolved_row(family=None, position=None, side=None, item_id="MLC123", year=2010):
    return {
        "ok": True, "item_id": item_id, "category_id": "MLC161582",
        "user_product_id": "MLCU" + item_id[3:], "year": year,
        "family_product_count": 1, "familia": family,
        "posicion_dt": position, "posicion_id": side,
        "product_family": {
            "domain_id": settings.ml_domain_id, "creation_source": "DEFAULT",
            "attributes": [
                {"id": "BRAND", "value_id": "100"},
                {"id": "CAR_AND_VAN_MODEL", "value_id": "200"},
                {"id": "YEAR", "value_name": str(year)},
                {"id": "CAR_AND_VAN_SUBMODEL", "value_id": "400"},
                {"id": "CAR_AND_VAN_ENGINE", "value_id": "500"},
                {"id": "TRANSMISSION_CONTROL_TYPE", "value_id": "600"},
            ],
        },
    }


class IndependentPositionTests(unittest.TestCase):
    def test_twenty_patterns_for_every_family_use_only_supplied_values(self):
        for family in FAMILIES:
            for position in POSITIONS:
                for side in SIDES:
                    with self.subTest(family=family, position=position, side=side):
                        restrictions = build_restrictions(family, position, side)
                        self.assertEqual(combinations(restrictions), expected(position, side))
                        if not position and not side:
                            self.assertEqual(restrictions, [])
                        else:
                            self.assertEqual(len(restrictions[0]["attribute_values"]), len(expected(position, side)))
                            for combination in restrictions[0]["attribute_values"]:
                                for value in combination["values"]:
                                    self.assertEqual(value["value_name"], NAMES[value["value_id"]])

    def test_blank_first_row_and_mixed_families_preserve_later_positions(self):
        rows = [
            resolved_row(), resolved_row("DESCONOCIDA", "DELANTERA", "IZQUIERDA"),
            resolved_row("BANDEJA SUSPENSION", "DELANTERA", "DERECHA"),
            resolved_row(None, "DELANTERA", "IZQUIERDA/DERECHA"),
            resolved_row("MLC-VEHICLE_WHEELS_BEARINGS", None, "DERECHA"),
            resolved_row(item_id="MLC456"), resolved_row(year=2011),
        ]
        grouped = build_grouped_product_families(rows)
        self.assertEqual(combinations(grouped["MLC123"][0]["payload"]["restrictions"]), {
            frozenset({"13701104", "2262158"}), frozenset({"13701104", "2262160"}),
            frozenset({"2262160"}),
        })
        self.assertNotIn("restrictions", grouped["MLC123"][1]["payload"])
        self.assertNotIn("restrictions", grouped["MLC456"][0]["payload"])


class IndependentPositionFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(settings, "compatibility_family_cache_ttl_seconds", 0))
        self.enterContext(patch.object(settings, "compatibility_enable_copy", False))
        self.enterContext(patch.object(settings, "compatibility_chunk_pause_seconds", 0))

    async def test_all_twenty_patterns_reach_post_and_put_with_blank_or_arbitrary_family(self):
        expected_by_target = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "independent-positions.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            assert worksheet is not None
            worksheet.title = "Hoja1"
            worksheet.append([
                "ASOCIACION ML", "MARCA", "MODELO", "AÑO", "VERSION", "CILINDRADA",
                "TRANSMISION", "FAMILIA", "DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE", "IZQUIERDA/DERECHA",
            ])
            for family in (None, "BANDEJA SUSPENSION", "MLC-VEHICLE_BRAKE_PADS"):
                for position in POSITIONS:
                    for side in SIDES:
                        item = "MLC" + str(len(expected_by_target) + 1)
                        worksheet.append([
                            item, "Suzuki", "Alto", 2007, "GL 800", "0.8L L3 Gasolina", "Manual",
                            family, position or "   ", side or "   ",
                        ])
                        expected_by_target[f"/user-products/MLCU{item[3:]}/compatibilities"] = expected(position, side)
            workbook.save(path)
            workbook.close()
            rows = load_excel_rows(str(path))

        catalog = SimpleNamespace(preload_all=AsyncMock(return_value=SimpleNamespace(stats=lambda: {})))

        def response(method, path, **_kwargs):
            if method == "GET" and path.startswith("/items/"):
                return {"category_id": "MLC161582", "user_product_id": "MLCU" + path.rsplit("/", 1)[-1][3:]}
            if path == "/catalog_compatibilities/products_search/count_family_products":
                return {"count": 1}
            if path in expected_by_target and method == "POST":
                return {"created_compatibilities_count": 0}
            if path in expected_by_target and method == "PUT":
                return {"update": {}}
            raise AssertionError((method, path))

        with (
            patch("services.compatibility_orchestrator_service.CatalogPreloadService", return_value=catalog),
            patch("services.compatibility_orchestrator_service.JobStore.update"),
            patch.object(settings, "compatibility_use_value_names", True),
            patch.object(ml_client, "request", new=AsyncMock(side_effect=response)) as request,
        ):
            outcome = await process_excel_compatibilities_end_to_end(
                job_id="independent-positions-test", access_token="token", user_id="123", rows=rows,
            )
        self.assertEqual(len(outcome["results"]), 60)
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        writes = [call for call in request.await_args_list if call.args[1] in expected_by_target]
        self.assertEqual(len(writes), 120)
        for target, expected_values in expected_by_target.items():
            matching = [call for call in writes if call.args[1] == target]
            self.assertEqual([call.args[0] for call in matching], ["POST", "PUT"])
            for call in matching:
                body = call.kwargs["json_body"]
                families = body["products_families"] if call.args[0] == "POST" else body["update"]["products_families"]
                payload = families[0]
                self.assertEqual(combinations(payload.get("restrictions", [])), expected_values)
                if not expected_values:
                    self.assertNotIn("restrictions", payload)

    async def test_unknown_family_errors_are_reported_before_writing(self):
        for family in (None, "DESCONOCIDA", "BANDEJA SUSPENSION"):
            for position, side in (("DELANTERO", "IZQUIERDA"), ("", "AMBAS"), ("", "IZQUIERDA/")):
                with self.subTest(family=family, position=position, side=side):
                    with patch.object(ml_client, "request", new=AsyncMock()) as request:
                        outcome = await process_compatibility_batches(
                            access_token="token", user_id="123", rows=[resolved_row(family, position, side)],
                        )
                    request.assert_not_awaited()
                    self.assertEqual(outcome["results"][0]["error_code"], "INVALID_POSITION")

    async def test_positions_survive_chunks_even_when_next_row_has_no_positions(self):
        state = CompatibilityBatchState()

        def response(method, _path, **_kwargs):
            return {"created_compatibilities_count": 1} if method == "POST" else {"update": {}}

        with patch.object(ml_client, "request", new=AsyncMock(side_effect=response)) as request:
            for row in (
                resolved_row(None, None, "IZQUIERDA"),
                resolved_row("DESCONOCIDA", None, "DERECHA"), resolved_row(),
            ):
                outcome = await process_compatibility_batches(
                    access_token="token", user_id="123", rows=[row], state=state,
                )
                self.assertTrue(outcome["results"][0]["ok"])
        self.assertEqual([call.args[0] for call in request.await_args_list], ["POST", "PUT", "PUT"])
        for call in request.await_args_list[1:]:
            payload = call.kwargs["json_body"]["update"]["products_families"][0]
            self.assertEqual(combinations(payload["restrictions"]), {
                frozenset({"2262158"}), frozenset({"2262160"}),
            })

    async def test_legacy_products_retain_positions_per_vehicle_in_the_same_listing(self):
        rows = []
        for product_id, position, side in (
            ("P1", "DELANTERA", "IZQUIERDA"), ("P1", "DELANTERA", "DERECHA"),
            ("P2", None, "DERECHA"), ("P3", None, None),
        ):
            row = resolved_row(None, position, side)
            row.pop("product_family")
            row["product_id"] = product_id
            rows.append(row)

        def response(method, _path, **kwargs):
            if method == "GET":
                return {"category_id": "MLC161582", "user_product_id": "MLCU123"}
            return {"created_compatibilities_count": len(kwargs["json_body"]["products"])}

        with patch.object(ml_client, "request", new=AsyncMock(side_effect=response)) as request:
            outcome = await process_compatibility_batches(access_token="token", user_id="123", rows=rows)
        self.assertTrue(all(row["ok"] for row in outcome["results"]))
        products = {product["id"]: product for call in request.await_args_list if call.args[0] == "POST"
                    for product in call.kwargs["json_body"]["products"]}
        self.assertEqual(combinations(products["P1"]["restrictions"]), {
            frozenset({"13701104", "2262158"}), frozenset({"13701104", "2262160"}),
        })
        self.assertEqual(combinations(products["P2"]["restrictions"]), {frozenset({"2262160"})})
        self.assertNotIn("restrictions", products["P3"])


if __name__ == "__main__":
    unittest.main()
