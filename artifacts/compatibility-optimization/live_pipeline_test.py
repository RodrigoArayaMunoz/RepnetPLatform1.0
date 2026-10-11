"""Run authorized real tests through the optimized backend with Excel rows."""
import asyncio
import argparse
import json
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))
from config import settings
from services.compatibility_batch_service import DEFAULT_COMPATIBILITY_NOTE, build_restrictions
from services.compatibility_orchestrator_service import process_excel_compatibilities_end_to_end
from services.compatibility_service import SimpleRateLimiter
from services.excel_service import get_row_value, load_excel_rows
from services.ml_client import ml_client
from services.supabase_meli_connection_store import supabase_meli_connection_store

OUTPUT = Path(__file__).parent

def positions(restrictions):
    return {frozenset(value["value_id"] for value in combination["values"])
            for restriction in restrictions for combination in restriction["attribute_values"]}

async def main(resume_only=False):
    preflight = json.loads((OUTPUT / "live-preflight.json").read_text(encoding="utf-8"))
    connection = next(c for c in await supabase_meli_connection_store.list_rows(include_tokens=True)
                      if c.get("is_active") and c.get("access_token")
                      and supabase_meli_connection_store._parse_expires_at(c.get("expires_at")) > time.time())
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "requests": [], "scenarios": []}
    allowed_items = {entry["item_id"] for entry in preflight["candidates"]}
    allowed_user_products = {entry["user_product_id"] for entry in preflight["candidates"]}
    rows = load_excel_rows(r"C:\Users\Rodrigo Araya\Downloads\RESULTADO MLC-AMORTIGUADORES.xlsx")
    toyota_row = next(row for row in rows if get_row_value(row, "MARCA") == "Toyota")
    allowed_items.add(get_row_value(toyota_row, "ASOCIACION ML"))
    writes = 0
    def save():
        filename = "live-resume-verification.json" if resume_only else "live-pipeline-verification.json"
        (OUTPUT / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def log_request(request):
        nonlocal writes
        path = request.url.path
        if path.startswith("/items/"):
            assert path.rsplit("/", 1)[-1] in allowed_items and request.method == "GET"
        elif path.startswith("/user-products/"):
            assert path.split("/")[2] in allowed_user_products
            assert request.method in {"GET", "POST", "PUT"}
            if request.method != "GET":
                writes += 1
                assert writes <= (0 if resume_only else 12), "Small live test write budget exceeded"
        else:
            assert path.startswith("/catalog_") and request.method == "POST"
        entry = {"method": request.method, "path": path}
        if request.content:
            entry["body"] = json.loads(request.content)
        report["requests"].append(entry)
        request.extensions["audit_index"] = len(report["requests"]) - 1
        save()

    async def log_response(response):
        await response.aread()
        report["requests"][response.request.extensions["audit_index"]]["status"] = response.status_code
        if response.request.url.path.startswith("/items/") and response.status_code == 200:
            allowed_user_products.add(response.json()["user_product_id"])
        save()

    limiter = SimpleRateLimiter(0.7)
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "upload_dir", str(OUTPUT / "live-cache.local")))
        stack.enter_context(patch("services.compatibility_orchestrator_service.JobStore.update"))
        stack.enter_context(patch.object(ml_client, "get_valid_token", new=AsyncMock(return_value=connection["access_token"])))
        for module in ["compatibility_service", "compatibility_batch_service", "compatibility_copy_service"]:
            for name in ["READ_RATE_LIMITER", "COMPATIBILITY_WRITE_RATE_LIMITER"]:
                stack.enter_context(patch("services." + module + "." + name, limiter, create=True))
        await ml_client.startup()
        assert ml_client.client is not None
        ml_client.client.event_hooks = {"request": [log_request], "response": [log_response]}
        try:
            async def run(label, selected):
                start_index = len(report["requests"])
                started = time.monotonic()
                outcome = await process_excel_compatibilities_end_to_end(
                    job_id="live-optimized-" + label, access_token=connection["access_token"],
                    user_id=connection["ml_user_id"], rows=selected,
                )
                scenario = {"label": label, "elapsed_seconds": round(time.monotonic() - started, 3),
                            "results": outcome["results"], "batch_results": outcome["batch_results"],
                            "summary": outcome["summary"]}
                scenario_requests = report["requests"][start_index:]
                scenario["writes"] = [{"method": entry["method"], "path": entry["path"]} for entry in scenario_requests
                                      if entry["path"].startswith("/user-products/") and entry["method"] in {"POST", "PUT"}]
                report["scenarios"].append(scenario)
                save()
                assert all(result["ok"] for result in outcome["results"]), scenario
                for row, result in zip(selected, outcome["results"]):
                    after = await ml_client.get_user_product_compatibilities(
                        access_token=connection["access_token"], user_id=connection["ml_user_id"],
                        user_product_id=result["user_product_id"], rate_limiter=limiter,
                    )
                    expected = positions(build_restrictions(get_row_value(row, "FAMILIA"), get_row_value(row, "POSICION_DT"), get_row_value(row, "POSICION_ID")))
                    assert after["products"]
                    assert all(product["note"] == DEFAULT_COMPATIBILITY_NOTE and positions(product["restrictions"]) == expected
                               for product in after["products"])
                    scenario.setdefault("read_back", []).append({"item_id": get_row_value(row, "ASOCIACION ML"), "products": after["products"]})
                save()
                print(json.dumps({"scenario": label, "rows": len(selected), "writes": scenario["writes"], "created": outcome["summary"]["total_created_compatibilities"], "elapsed_seconds": scenario["elapsed_seconds"]}), flush=True)
                return scenario

            copy_rows = [entry["samples"][0]["row"] for entry in preflight["candidates"] if entry["purpose"] == "COPY_IDENTICAL_FULL_SET"]
            existing_rows = [entry["samples"][0]["row"] for entry in preflight["candidates"][:3]]
            if resume_only:
                for label, selected in [("existing_positions_and_note_resumed", existing_rows),
                                        ("creation_and_copy_resumed", copy_rows),
                                        ("multiple_products_resumed", [toyota_row])]:
                    resumed = await run(label, selected)
                    assert resumed["writes"] == []
                report["finished_at"] = datetime.now(timezone.utc).isoformat()
                report["compatibility_write_count"] = writes
                report["all_scenarios_passed"] = True
                save()
                return
            copy_scenario = await run("creation_and_copy", copy_rows)
            assert any(batch.get("write_strategy") == "copy" for batch in copy_scenario["batch_results"])
            repeated = await run("resume_without_writes", copy_rows)
            assert repeated["writes"] == []
            await run("existing_positions_and_note", existing_rows)
            toyota = await run("multiple_products_per_family", [toyota_row])
            assert len(toyota["read_back"][0]["products"]) == 4
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            report["compatibility_write_count"] = writes
            report["all_scenarios_passed"] = True
            save()
        finally:
            await ml_client.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-only", action="store_true", help="Verify existing checkpoints; reject all compatibility writes.")
    asyncio.run(main(resume_only=parser.parse_args().resume_only))
