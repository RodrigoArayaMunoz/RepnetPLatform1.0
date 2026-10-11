"""Verify grouping and positions on the complete workbook without API calls.

Catalog counts are simulated as one product per family; this is not a live
catalog audit or a prediction of the number of real Mercado Libre requests.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))

from config import settings
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.excel_service import get_row_value, load_excel_rows
from services.ml_client import ml_client
from tests.test_compatibility_optimization import StatefulCompatibilityAPI

OUTPUT = Path(__file__).parent


def position_sets(restrictions):
    return {frozenset(value["value_id"] for value in combination["values"])
            for restriction in restrictions for combination in restriction["attribute_values"]}


async def main(excel_path):
    rows = load_excel_rows(str(excel_path))
    items = json.loads((OUTPUT.parent / "shock-absorber-compatibilities/mercadolibre-items-verification.json").read_text(encoding="utf-8"))
    metadata = {item["id"]: item for item in items["items"]}
    api = StatefulCompatibilityAPI()
    api.count = 1

    async def request(method, path, **kwargs):
        if method == "GET" and path.startswith("/items/"):
            return metadata[path.rsplit("/", 1)[-1]]
        return await api.request(method, path, **kwargs)

    started = time.monotonic()
    with (patch.object(settings, "compatibility_use_value_names", True),
          patch.object(settings, "compatibility_enable_copy", True),
          patch.object(settings, "compatibility_family_cache_ttl_seconds", 0),
          patch.object(settings, "compatibility_chunk_pause_seconds", 0),
          patch("services.compatibility_orchestrator_service.JobStore.update"),
          patch.object(ml_client, "request", new=AsyncMock(side_effect=request))):
        outcome = await process_excel_compatibilities_end_to_end(
            job_id="complete-workbook-simulation", access_token="mock-token", user_id="mock-user", rows=rows,
        )
    assert len(outcome["results"]) == len(rows)
    assert all(result["ok"] for result in outcome["results"])
    for index, (row, result) in enumerate(zip(rows, outcome["results"])):
        assert result["original_row_index"] == index
        up = metadata[get_row_value(row, "ASOCIACION ML")]["user_product_id"]
        product_id = api.product_ids(result["product_family"]["attributes"], 1)[0]
        product = api.listings[up][product_id]
        primary = {"DELANTERA": "13701104", "TRASERA": "13701105"}.get(get_row_value(row, "POSICION_DT"))
        expected = {frozenset(([primary] if primary else []) + [side]) for side in ["2262158", "2262160"]}
        assert expected.issubset(position_sets(product["restrictions"])), index
    writes = api.writes()
    family_writes = [write for write in writes if "products_families" in write["body"]]
    report = {"mode": "complete_workbook_simulation", "source_file": excel_path.name,
              "catalog_count_assumption": "one simulated product per family", "live_write_count": 0,
              "verified_rows": len(rows), "success_count": outcome["summary"]["success_count"],
              "item_count": len(api.listings), "count_family_queries": len(api.count_calls()),
              "family_post_count": len(family_writes),
              "copy_post_count": len(writes) - len(family_writes),
              "put_count": sum(write["method"] == "PUT" for write in writes),
              "total_compatibility_write_count": len(writes),
              "max_families_in_request": max(len(write["body"]["products_families"]) for write in family_writes),
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    (OUTPUT / "full-pipeline-simulation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", type=Path)
    asyncio.run(main(parser.parse_args().excel))
