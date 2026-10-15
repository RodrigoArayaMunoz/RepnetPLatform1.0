"""Audit every Excel position and simulate sample writes; optional GET-only ML checks."""

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))

from config import settings
from services.compatibility_batch_service import build_restrictions
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.excel_service import get_row_value, load_excel_rows, normalize_transmission
from services.ml_client import ml_client
from services.supabase_meli_connection_store import supabase_meli_connection_store
from services.token_store import token_store

OUTPUT = Path(__file__).resolve().parent
POSITION_IDS = {
    "DELANTERA": "13701104", "TRASERA": "13701105",
    "CONDUCTOR": "13373175", "ACOMPAÑANTE": "13373176",
}
SIDE_IDS = {"IZQUIERDA": "2262158", "DERECHA": "2262160"}
NAMES = {
    "13701104": "Delantera", "13701105": "Trasera",
    "13373175": "Conductor", "13373176": "Acompañante",
    "2262158": "Izquierda", "2262160": "Derecha",
}
VEHICLE_COLUMNS = {
    "BRAND": "MARCA", "CAR_AND_VAN_MODEL": "MODELO", "YEAR": "AÑO",
    "CAR_AND_VAN_SUBMODEL": "VERSION", "CAR_AND_VAN_ENGINE": "CILINDRADA",
    "TRANSMISSION_CONTROL_TYPE": "TRANSMISION",
}


def text(value):
    return str(value or "").strip()


def combinations(restrictions):
    return {
        frozenset(value["value_id"] for value in combination["values"])
        for restriction in restrictions
        for combination in restriction["attribute_values"]
    }


def expected_combinations(position, side):
    if not position and not side:
        return set()
    primary = [POSITION_IDS[position]] if position else []
    return {
        frozenset(primary + ([SIDE_IDS[value.strip()]] if value.strip() else []))
        for value in side.split("/")
    }


def vehicle_key(row):
    values = {attribute: text(get_row_value(row, column)) for attribute, column in VEHICLE_COLUMNS.items()}
    values["TRANSMISSION_CONTROL_TYPE"] = normalize_transmission(values["TRANSMISSION_CONTROL_TYPE"])
    return tuple(sorted(values.items()))


async def current_connection():
    connections = await supabase_meli_connection_store.list_rows(include_tokens=True)
    valid = [connection for connection in connections if connection.get("is_active")
             and connection.get("access_token")
             and supabase_meli_connection_store._parse_expires_at(connection.get("expires_at")) > time.time()]
    if valid:
        return valid[0]
    for user_id, token in token_store.load().items():
        if token.get("access_token") and float(token.get("expires_at", 0)) > time.time():
            return {"ml_user_id": user_id, "access_token": token["access_token"]}
    raise RuntimeError("No hay una conexión vigente de Mercado Libre")


async def check_ml(samples):
    connection = await current_connection()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "live_write_count": 0, "items": [], "domains": []}
    item_ids = list(dict.fromkeys(text(get_row_value(sample["row"], "ASOCIACION ML")) for sample in samples))
    async with httpx.AsyncClient(timeout=45, headers={"Authorization": "Bearer " + connection["access_token"]}) as client:
        for offset in range(0, len(item_ids), 20):
            response = await client.get(settings.ml_api_base + "/items", params={
                "ids": ",".join(item_ids[offset:offset + 20]),
                "attributes": "id,status,category_id,user_product_id,domain_id,seller_id",
            })
            response.raise_for_status()
            for result in response.json():
                if result["code"] != 200:
                    raise RuntimeError(f"Consulta de publicación fallida: {result['code']}")
                report["items"].append(result["body"])
        for domain in sorted({item["domain_id"] for item in report["items"]}):
            await asyncio.sleep(1)
            path = "/catalog_compatibilities/restrictions/values"
            params = {"main_domain_id": settings.ml_domain_id, "secondary_domain_id": domain}
            response = await client.get(settings.ml_api_base + path, params=params)
            response.raise_for_status()
            payload = response.json()
            allowed = {value["value_id"]: value["value_name"]
                       for attribute in payload["attributes_values"] if attribute["attribute_id"] == "POSITION"
                       for value in attribute["values"]}
            mismatches = {value_id: name for value_id, name in NAMES.items() if allowed.get(value_id) != name}
            report["domains"].append({"domain_id": domain, "status": response.status_code,
                                      "source": str(response.url), "response": payload, "id_mismatches": mismatches})
            print(json.dumps({"domain": domain, "status": response.status_code, "id_mismatches": mismatches}), flush=True)
    (OUTPUT / "mercadolibre-position-values.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert all(not domain["id_mismatches"] for domain in report["domains"])
    return report


async def audit(excel_path, remote=False):
    logging.disable(logging.CRITICAL)
    rows = load_excel_rows(str(excel_path))
    patterns = Counter()
    families = Counter()
    samples = {}
    issues = []
    for excel_row, row in enumerate(rows, 2):
        family = text(get_row_value(row, "FAMILIA"))
        position = text(get_row_value(row, "POSICION_DT")).upper()
        side = text(get_row_value(row, "POSICION_ID")).upper()
        patterns[(position, side)] += 1
        families[family] += 1
        samples.setdefault((family, position, side), {"excel_row": excel_row, "row": row})
        try:
            actual = build_restrictions(family, position, side)
            assert combinations(actual) == expected_combinations(position, side)
            for restriction in actual:
                for combination in restriction["attribute_values"]:
                    for value in combination["values"]:
                        assert NAMES[value["value_id"]] == value["value_name"]
        except (AssertionError, KeyError) as error:
            issues.append({"excel_row": excel_row, "item_id": row.get("ASOCIACION ML"), "error": str(error)})

    expected_by_target = {}
    for sample in samples.values():
        row = sample["row"]
        target = "/user-products/mock-" + text(get_row_value(row, "ASOCIACION ML")) + "/compatibilities"
        expected_by_target.setdefault(target, {}).setdefault(vehicle_key(row), set()).update(expected_combinations(
            text(get_row_value(row, "POSICION_DT")).upper(), text(get_row_value(row, "POSICION_ID")).upper(),
        ))

    def response(method, path, **_kwargs):
        if method == "GET" and path.startswith("/items/"):
            return {"category_id": "mock-category", "user_product_id": "mock-" + path.rsplit("/", 1)[-1]}
        if path == "/catalog_compatibilities/products_search/count_family_products":
            return {"count": 1}
        if path in expected_by_target and method == "POST":
            return {"created_compatibilities_count": 0}
        if path in expected_by_target and method == "PUT":
            return {"update": {}}
        raise AssertionError((method, path))

    catalog = SimpleNamespace(preload_all=AsyncMock(return_value=SimpleNamespace(stats=lambda: {})))
    with (
        patch.object(settings, "compatibility_family_cache_ttl_seconds", 0),
        patch.object(settings, "compatibility_enable_copy", False),
        patch.object(settings, "compatibility_use_value_names", True),
        patch("services.compatibility_orchestrator_service.CatalogPreloadService", return_value=catalog),
        patch("services.compatibility_orchestrator_service.JobStore.update"),
        patch.object(ml_client, "request", new=AsyncMock(side_effect=response)) as request,
    ):
        outcome = await process_excel_compatibilities_end_to_end(
            job_id="refax-position-audit", access_token="mock-token", user_id="mock-user",
            rows=[sample["row"] for sample in samples.values()],
        )
    assert len(outcome["results"]) == len(samples)
    assert all(row["ok"] for row in outcome["results"]), outcome["results"]
    writes = []
    for call in request.await_args_list:
        method, path = call.args[:2]
        if path not in expected_by_target:
            continue
        body = call.kwargs["json_body"]
        payloads = body["products_families"] if method == "POST" else body["update"]["products_families"]
        for payload in payloads:
            key = tuple(sorted((attribute["id"], attribute["value_name"]) for attribute in payload["attributes"]))
            expected = expected_by_target[path][key]
            assert combinations(payload.get("restrictions", [])) == expected
            if not expected:
                assert "restrictions" not in payload
        writes.append({"method": method, "path": path, "json_body": body})
    assert {write["method"] for write in writes} == {"POST", "PUT"}
    report = {
        "source_file": str(excel_path), "total_rows": len(rows),
        "total_items": len({get_row_value(row, "ASOCIACION ML") for row in rows}),
        "families": dict(families), "rows_with_positions": len(rows) - patterns[("", "")],
        "rows_without_positions": patterns[("", "")], "rows_matching_excel": len(rows) - len(issues),
        "patterns": [{"position": position or None, "side": side or None, "rows": count}
                     for (position, side), count in patterns.items()],
        "samples": list(samples.values()), "simulated_sample_rows": len(samples),
        "simulated_http_writes": writes, "live_write_count": 0, "issues": issues,
    }
    (OUTPUT / "excel-positions-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"samples", "simulated_http_writes", "issues"}}, ensure_ascii=True), flush=True)
    if remote:
        await check_ml(list(samples.values()))
    return not issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", type=Path)
    parser.add_argument("--check-ml", action="store_true", help="Consulta IDs y publicaciones por GET")
    arguments = parser.parse_args()
    raise SystemExit(0 if asyncio.run(audit(arguments.excel, arguments.check_ml)) else 1)
