"""Test selected Excel records against ML, with a saved preflight and readback."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))

from config import settings
from services.compatibility_batch_service import DEFAULT_COMPATIBILITY_NOTE
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.compatibility_service import SimpleRateLimiter
from services.excel_service import get_row_value, load_excel_rows
from services.ml_client import ml_client
from verify_positions import OUTPUT, combinations, current_connection, expected_combinations, text, vehicle_key


async def main(apply=False):
    audit = json.loads((OUTPUT / "excel-positions-validation.json").read_text(encoding="utf-8"))
    metadata = json.loads((OUTPUT / "mercadolibre-position-values.json").read_text(encoding="utf-8"))
    connection = await current_connection()
    user_id = connection["ml_user_id"]
    token = connection["access_token"]
    items = {item["id"]: item for item in metadata["items"]}
    assert all(str(item["seller_id"]) == str(user_id) for item in items.values())
    limiter = SimpleRateLimiter(0.8)
    write_limiter = SimpleRateLimiter(min(
        100 / 60, settings.ml_write_requests_per_second,
        settings.ml_compatibility_write_requests_per_second,
    ))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": "live" if apply else "preflight",
              "candidates": [], "requests": [], "live_write_count": 0}
    filename = "live-sample-verification.json" if apply else "live-preflight.json"

    def save():
        (OUTPUT / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    selected = []
    # One real row per position pattern, plus rows with a populated FAMILIA.
    seen = set()
    for sample in audit["samples"]:
        row = sample["row"]
        pattern = (text(get_row_value(row, "POSICION_DT")), text(get_row_value(row, "POSICION_ID")))
        if pattern not in seen or text(get_row_value(row, "FAMILIA")):
            selected.append(sample)
            seen.add(pattern)
    assert len(selected) <= 20
    selected_keys = {(sample["row"]["ASOCIACION ML"], vehicle_key(sample["row"])): set() for sample in selected}
    for row in load_excel_rows(audit["source_file"]):
        key = (row["ASOCIACION ML"], vehicle_key(row))
        if key in selected_keys:
            selected_keys[key].add((text(row.get("POSICION_DT")), text(row.get("POSICION_ID"))))
    assert all(len(patterns) == 1 for patterns in selected_keys.values()), "La muestra requiere posiciones adicionales del mismo vehículo"
    await ml_client.startup()
    # Use the active Supabase connection rather than the container's token file.
    auth_patch = patch.object(ml_client, "get_valid_token", new=AsyncMock(return_value=token))
    auth_patch.start()
    try:
        assert await ml_client.validate_token(token), "La conexión de Mercado Libre ya no está vigente"
        for sample in selected:
            row = sample["row"]
            item = items[get_row_value(row, "ASOCIACION ML")]
            before = await ml_client.get_user_product_compatibilities(
                access_token=token, user_id=user_id, user_product_id=item["user_product_id"], rate_limiter=limiter,
            )
            products = before.get("products")
            assert isinstance(products, list)
            candidate = {**sample, "item": item, "before": before,
                         "eligible": not products and not before.get("catalog_compatibilities_count", 0)}
            report["candidates"].append(candidate)
            print(json.dumps({"item": item["id"], "excel_row": sample["excel_row"],
                              "existing_products": len(products), "eligible": candidate["eligible"]}), flush=True)
            save()
        if not apply:
            return
        candidates = [candidate for candidate in report["candidates"] if candidate["eligible"]]
        assert candidates, "No hay publicaciones sin compatibilidades para esta muestra"
        allowed_targets = {"/user-products/" + candidate["item"]["user_product_id"] + "/compatibilities" for candidate in candidates}
        original_request = ml_client.request

        async def tracked_request(method, path, **kwargs):
            is_write = path.startswith("/user-products/") and method in {"POST", "PUT"}
            if is_write:
                assert path in allowed_targets
                report["live_write_count"] += 1
            entry = {"method": method, "path": path, "json_body": kwargs.get("json_body")}
            report["requests"].append(entry)
            save()
            response = await original_request(method, path, **kwargs)
            entry["response"] = response
            save()
            return response

        with (
            patch.object(settings, "compatibility_family_cache_ttl_seconds", 0),
            patch.object(settings, "compatibility_enable_copy", False),
            patch.object(settings, "compatibility_use_value_names", True),
            patch("services.compatibility_service.READ_RATE_LIMITER", limiter),
            patch("services.compatibility_batch_service.COMPATIBILITY_WRITE_RATE_LIMITER", write_limiter),
            patch("services.compatibility_orchestrator_service.JobStore.update"),
            patch.object(ml_client, "request", new=tracked_request),
        ):
            outcome = await process_excel_compatibilities_end_to_end(
                job_id="refax-live-sample", access_token=token, user_id=user_id,
                rows=[candidate["row"] for candidate in candidates],
            )
        report["results"] = outcome["results"]
        report["summary"] = outcome["summary"]
        save()
        verified = []
        for candidate, result in zip(candidates, outcome["results"]):
            if not result["ok"]:
                continue
            row = candidate["row"]
            after = await ml_client.get_user_product_compatibilities(
                access_token=token, user_id=user_id, user_product_id=candidate["item"]["user_product_id"], rate_limiter=limiter,
            )
            candidate["after"] = after
            expected = expected_combinations(text(get_row_value(row, "POSICION_DT")).upper(), text(get_row_value(row, "POSICION_ID")).upper())
            assert after["products"], candidate["item"]["id"]
            assert len(after["products"]) == result["family_product_count"]
            assert all(combinations(product.get("restrictions") or []) == expected
                       and product.get("note") == DEFAULT_COMPATIBILITY_NOTE for product in after["products"])
            verified.append({"item_id": candidate["item"]["id"], "excel_row": candidate["excel_row"],
                             "family": row.get("FAMILIA"), "position": row.get("POSICION_DT"), "side": row.get("POSICION_ID"),
                             "verified_compatibilities": len(after["products"])})
            report["verified"] = verified
            save()
            print(json.dumps(verified[-1], ensure_ascii=True), flush=True)
        assert all(result["ok"] for result in outcome["results"]), "Revisar errores de resolución en el informe"
        assert len(verified) == len(candidates)
        print(json.dumps({"verified_rows": len(verified), "verified_compatibilities": sum(row["verified_compatibilities"] for row in verified),
                          "live_write_count": report["live_write_count"]}), flush=True)
    finally:
        auth_patch.stop()
        await ml_client.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Crea las compatibilidades de muestra y verifica por GET")
    asyncio.run(main(parser.parse_args().apply))
