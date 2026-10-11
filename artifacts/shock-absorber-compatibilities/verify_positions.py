"""Audit Excel positions and simulated HTTP bodies without modifying listings."""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src" / "backend" / "compatibilties"))

from config import settings
from services.compatibility_batch_service import build_restrictions
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.excel_service import get_row_value, load_excel_rows
from services.ml_client import ml_client

OUTPUT = Path(__file__).resolve().parent
POSITION_IDS = {
    "DELANTERA": "13701104", "TRASERA": "13701105",
    "CONDUCTOR": "13373175", "ACOMPAÑANTE": "13373176",
}
SIDE_IDS = {"IZQUIERDA": "2262158", "DERECHA": "2262160"}


def combinations(restrictions: list[dict]) -> set[frozenset[str]]:
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def expected_combinations(position: str, side: str) -> set[frozenset[str]]:
    primary = [POSITION_IDS[position]] if position else []
    sides = [value.strip() for value in side.split("/")] if side else [""]
    return {
        frozenset(primary + ([SIDE_IDS[value]] if value else []))
        for value in sides
    }


async def audit(excel_path: Path) -> bool:
    logging.disable(logging.CRITICAL)
    rows = load_excel_rows(str(excel_path))
    metadata = json.loads((OUTPUT / "mercadolibre-position-values.json").read_text(encoding="utf-8"))
    allowed_ids = {
        value["value_id"]: value["value_name"]
        for value in metadata["response"]["attributes_values"][0]["values"]
    }
    patterns = Counter()
    samples = {}
    issues = []
    correct_rows = 0
    for excel_row, row in enumerate(rows, 2):
        family = get_row_value(row, "FAMILIA")
        position = str(get_row_value(row, "POSICION_DT") or "").strip().upper()
        side = str(get_row_value(row, "POSICION_ID") or "").strip().upper()
        assert family == "MLC-VEHICLE_SHOCK_ABSORBERS"
        restrictions = build_restrictions(family, position, side)
        for restriction in restrictions:
            for combination in restriction["attribute_values"]:
                for value in combination["values"]:
                    assert allowed_ids[value["value_id"]] == value["value_name"], excel_row
        expected = expected_combinations(position, side)
        matches = combinations(restrictions) == expected
        correct_rows += int(matches)
        pattern = (position, side)
        patterns[pattern] += 1
        samples.setdefault(pattern, {"excel_row": excel_row, "row": row})
        if not matches:
            issues.append({
                "excel_row": excel_row,
                "item_id": get_row_value(row, "ASOCIACION ML"),
                "position": position or None, "side": side or None,
                "actual_restrictions": restrictions,
                "expected_position_value_ids": [sorted(values) for values in sorted(expected, key=lambda values: sorted(values))],
            })

    items_report = json.loads((OUTPUT / "mercadolibre-items-verification.json").read_text(encoding="utf-8"))
    item_metadata = {item["id"]: item for item in items_report["items"]}

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
            return item_metadata[path.rsplit("/", 1)[-1]]
        if path == "/catalog_compatibilities/products_search/count_family_products":
            return {"count": 1}
        if path.startswith("/user-products/"):
            if method == "POST":
                return {"created_compatibilities_count": len(kwargs["json_body"]["products_families"])}
            return {"update": {}}
        raise AssertionError(f"Unexpected mocked request: {method} {path}")

    catalog = SimpleNamespace(
        preload_all=AsyncMock(return_value=SimpleNamespace(stats=lambda: {})),
        resolve_vehicle_attribute_ids=resolve_attributes,
    )
    with (
        patch.object(settings, "compatibility_family_cache_ttl_seconds", 0),
        patch.object(settings, "compatibility_enable_copy", False),
        patch("services.compatibility_orchestrator_service.CatalogPreloadService", return_value=catalog),
        patch("services.compatibility_orchestrator_service.JobStore.update"),
        patch.object(ml_client, "request", new=AsyncMock(side_effect=request_response)) as request,
    ):
        outcome = await process_excel_compatibilities_end_to_end(
            job_id="shock-position-audit", access_token="mock-token", user_id="mock-user",
            rows=[sample["row"] for sample in samples.values()],
        )
    assert len(outcome["results"]) == len(samples)
    assert all(row["ok"] for row in outcome["results"])
    writes = [
        {"method": call.args[0], "path": call.args[1], "json_body": call.kwargs["json_body"]}
        for call in request.await_args_list if call.args[1].startswith("/user-products/") and call.args[0] in {"POST", "PUT"}
    ]
    for sample in samples.values():
        item_id = get_row_value(sample["row"], "ASOCIACION ML")
        target = "/user-products/" + item_metadata[item_id]["user_product_id"] + "/compatibilities"
        matching_writes = [write for write in writes if write["path"] == target]
        assert {write["method"] for write in matching_writes} == {"POST"}
        for write in matching_writes:
            body = write["json_body"]
            families = body["products_families"] if write["method"] == "POST" else body["update"]["products_families"]
            actual = families[0].get("restrictions", [])
            expected_actual = expected_combinations(
                str(get_row_value(sample["row"], "POSICION_DT") or "").strip().upper(),
                str(get_row_value(sample["row"], "POSICION_ID") or "").strip().upper(),
            )
            assert combinations(actual) == expected_actual, sample["excel_row"]
            for restriction in actual:
                for combination in restriction["attribute_values"]:
                    for value in combination["values"]:
                        assert allowed_ids[value["value_id"]] == value["value_name"]

    report = {
        "source_file": excel_path.name, "mode": "verify_corrected_behavior",
        "total_rows": len(rows), "position_rows_matching_excel": correct_rows,
        "position_rows_not_matching_excel": len(issues),
        "affected_item_ids": sorted({issue["item_id"] for issue in issues}),
        "patterns": [{"position": key[0] or None, "side": key[1] or None, "rows": count} for key, count in patterns.items()],
        "ids_verified_against_live_ml": True,
        "position_metadata_checked_at": metadata["checked_at"],
        "position_metadata_source": metadata["source"],
        "position_metadata_read_from": "mercadolibre-position-values.json",
        "simulated_http_write_count": len(writes), "live_write_count": 0,
        "simulated_http_writes": writes, "issues": issues,
    }
    (OUTPUT / "excel-positions-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("simulated_http_writes", "issues")}, ensure_ascii=True))
    return not issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", type=Path)
    raise SystemExit(0 if asyncio.run(audit(parser.parse_args().excel)) else 1)
