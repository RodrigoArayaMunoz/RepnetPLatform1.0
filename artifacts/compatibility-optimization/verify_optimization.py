"""Read-only ML searches and local request estimates; never writes compatibilities."""

import asyncio
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import sys

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))

import httpx

from config import settings
from services.catalog_preload_service import CatalogPreloadService
from services.compatibility_service import JobMetrics, build_vehicle_family_attributes, vehicle_resolution_key
from services.excel_service import get_row_value, load_excel_rows
from services.process_chunking_service import get_compatibility_chunk_size
from services.supabase_meli_connection_store import supabase_meli_connection_store


OUTPUT = Path(__file__).resolve().parent
EXCEL = Path(r"C:\Users\Rodrigo Araya\Downloads\RESULTADO MLC-AMORTIGUADORES.xlsx")
ATTRIBUTE_COLUMNS = [
    ("BRAND", "MARCA"), ("CAR_AND_VAN_MODEL", "MODELO"), ("YEAR", "AÑO"),
    ("CAR_AND_VAN_SUBMODEL", "VERSION"), ("CAR_AND_VAN_ENGINE", "CILINDRADA"),
    ("TRANSMISSION_CONTROL_TYPE", "TRANSMISION"),
]
COUNT_PATH = "/catalog_compatibilities/products_search/count_family_products"


def name_attributes(row):
    return [{"id": attribute, "value_name": str(get_row_value(row, column)).strip()}
            for attribute, column in ATTRIBUTE_COLUMNS]


async def main():
    rows = load_excel_rows(str(EXCEL))
    keys = [vehicle_resolution_key(row) for row in rows]
    vehicles = {key[1:] for key in keys}
    by_item = defaultdict(set)
    for row, key in zip(rows, keys):
        by_item[key[0]].add((key[1:], str(get_row_value(row, "POSICION_DT") or "").strip().upper(),
                           str(get_row_value(row, "POSICION_ID") or "").strip().upper()))
    groups = Counter(frozenset(values) for values in by_item.values())
    chunk_size = get_compatibility_chunk_size()
    batch_floor = sum(sum(ceil(count / 10) for count in Counter(key[0] for key in keys[start:start + chunk_size]).values())
                      for start in range(0, len(keys), chunk_size))
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "source_file": EXCEL.name,
        "rows": len(rows), "unique_item_vehicle_pairs": len(set(keys)),
        "unique_vehicle_specs_without_item": len(vehicles), "unique_items": len(by_item),
        "distinct_vehicle_and_position_sets": len(groups),
        "items_reusable_from_identical_set": sum(size - 1 for size in groups.values()),
        "request_estimates": {
            "assumption": "At most 10 families per batch, and their combined matched products fit the 200-product limit; these are lower bounds, not measured API writes.",
            "current_post_put": batch_floor * 2,
            "post_only_same_order": batch_floor,
            "post_only_group_whole_item": sum(ceil(len(values) / 10) for values in by_item.values()),
            "unique_named_catalog_prefixes": {
                "MODEL": len({(key[1],) for key in keys}),
                "YEAR": len({(key[1], key[2]) for key in keys}),
                "SUBMODEL": len({(key[1], key[2], key[6]) for key in keys}),
                "ENGINE": len({(key[1], key[2], key[6], key[3]) for key in keys}),
                "TRANSMISSION": len({(key[1], key[2], key[6], key[3], key[4]) for key in keys}),
            },
        },
        "live_searches": [], "live_compatibility_writes": 0,
        "positions_by_name_only": "Not verified; documented restrictions use closed value_id sets, current mapping is local.",
        "post_only_creation": "Supported by official request schema; not tested against live compatibility writes.",
    }
    connections = await supabase_meli_connection_store.list_rows(include_tokens=True)
    connection = next((entry for entry in connections if entry.get("is_active") and entry.get("access_token")
                       and supabase_meli_connection_store._parse_expires_at(entry.get("expires_at")) > time.time()), None)
    if connection is None:
        raise RuntimeError("No active unexpired token available for the read-only verification")
    headers = {"Authorization": "Bearer " + connection["access_token"]}
    async with httpx.AsyncClient(base_url=settings.ml_api_base, headers=headers, timeout=30) as client:
        last_request = 0.0

        async def post_search(path, body):
            nonlocal last_request
            if path != COUNT_PATH and not (path.startswith("/catalog_domains/") and path.endswith("/top_values")):
                raise AssertionError("Only non-mutating catalog search resources are allowed")
            await asyncio.sleep(max(0, 1.3 - (time.monotonic() - last_request)))
            last_request = time.monotonic()
            response = await client.post(path, json=body)
            data = response.json()
            if response.status_code >= 400:
                raise RuntimeError(f"Read-only search HTTP {response.status_code}: {data.get('message', '')}")
            return data

        async def catalog_call(function, access_token, attribute_id, **kwargs):
            path = f"/catalog_domains/{settings.ml_domain_id}/attributes/{attribute_id}/top_values"
            known = kwargs.get("known_attributes")
            data = await post_search(path, {"known_attributes": known} if known else {})
            if isinstance(data, list):
                return data
            return data.get("top_values", data.get("results", data.get("values", [])))

        catalog = CatalogPreloadService(call_ml=catalog_call, metrics=JobMetrics())
        await catalog.preload_all("unused-in-read-only-wrapper")
        samples = {}
        for row in rows:
            brand = str(get_row_value(row, "MARCA"))
            samples.setdefault(brand, row)
            if len(samples) == 6:
                break
        for index, row in enumerate(samples.values()):
            names = name_attributes(row)
            named_response = await post_search(COUNT_PATH, {"domain_id": settings.ml_domain_id, "attributes": names})
            entry = {"attributes_by_name": names, "names_status": 200, "names_count": named_response.get("count")}
            if index < 3:
                ids = await catalog.resolve_vehicle_attribute_ids(
                    access_token="unused-in-read-only-wrapper", user_id=None,
                    brand_name=str(get_row_value(row, "MARCA")), model_name=str(get_row_value(row, "MODELO")),
                    year=int(get_row_value(row, "AÑO")), version_name=str(get_row_value(row, "VERSION")),
                    engine_name=str(get_row_value(row, "CILINDRADA")), transmission_name=str(get_row_value(row, "TRANSMISION")),
                )
                if not all(ids.values()):
                    entry["id_resolution_incomplete"] = True
                else:
                    attrs = build_vehicle_family_attributes(**{key: value for key, value in ids.items() if value is not None})
                    id_response = await post_search(COUNT_PATH, {"domain_id": settings.ml_domain_id, "attributes": attrs})
                    entry.update({"attributes_by_id": attrs, "ids_count": id_response.get("count"),
                                  "same_count": id_response.get("count") == named_response.get("count")})
            report["live_searches"].append(entry)
            print(json.dumps({"sample": index + 1, "brand": get_row_value(row, "MARCA"),
                              "names_count": entry["names_count"], "ids_count": entry.get("ids_count"),
                              "same_count": entry.get("same_count")}), flush=True)
    (OUTPUT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "live_searches"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
