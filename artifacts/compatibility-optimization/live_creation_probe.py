"""Authorized small creation experiment using exact rows from the supplied Excel."""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "src/backend/compatibilties"))
import httpx
from config import settings
from services.compatibility_batch_service import DEFAULT_COMPATIBILITY_NOTE, build_restrictions
from services.excel_service import get_row_value
from services.supabase_meli_connection_store import supabase_meli_connection_store

OUTPUT = Path(__file__).parent
COLUMNS = [("BRAND", "MARCA"), ("CAR_AND_VAN_MODEL", "MODELO"), ("YEAR", "AÑO"),
           ("CAR_AND_VAN_SUBMODEL", "VERSION"), ("CAR_AND_VAN_ENGINE", "CILINDRADA"),
           ("TRANSMISSION_CONTROL_TYPE", "TRANSMISION")]

async def main():
    preflight = json.loads((OUTPUT / "live-preflight.json").read_text(encoding="utf-8"))
    candidates = preflight["candidates"][:3]
    connections = await supabase_meli_connection_store.list_rows(include_tokens=True)
    connection = next(c for c in connections if c.get("is_active") and c.get("access_token")
                      and supabase_meli_connection_store._parse_expires_at(c.get("expires_at")) > time.time())
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "experiments": []}
    def save():
        (OUTPUT / "live-creation-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    async with httpx.AsyncClient(base_url=settings.ml_api_base, headers={"Authorization": "Bearer " + connection["access_token"]}, timeout=45) as client:
        for candidate in candidates:
            assert candidate["status"] == "active" and candidate["compatibilities"] == {"products": []}
            row = candidate["samples"][0]["row"]
            family = {"domain_id": settings.ml_domain_id, "creation_source": "DEFAULT",
                      "attributes": [{"id": attr, "value_name": str(get_row_value(row, col)).strip()} for attr, col in COLUMNS],
                      "note": DEFAULT_COMPATIBILITY_NOTE,
                      "restrictions": build_restrictions(get_row_value(row, "FAMILIA"), get_row_value(row, "POSICION_DT"), get_row_value(row, "POSICION_ID"))}
            body = {"domain_id": settings.ml_domain_id, "category_id": candidate["category_id"], "products_families": [family]}
            path = "/user-products/" + candidate["user_product_id"] + "/compatibilities"
            experiment = {"item_id": candidate["item_id"], "purpose": candidate["purpose"], "before": candidate["compatibilities"], "post_body": body, "status": "pending"}
            report["experiments"].append(experiment)
            save()
            response = await client.post(path, json=body)
            experiment.update({"post_status": response.status_code, "post_response": response.json()})
            save()
            response.raise_for_status()
            await asyncio.sleep(1.5)
            response = await client.get(path, params={"main_domain_id": settings.ml_domain_id, "extended": "true"})
            experiment.update({"get_status": response.status_code, "after": response.json(), "status": "read_back"})
            save()
            response.raise_for_status()
            print(json.dumps({"item_id": candidate["item_id"], "post": experiment["post_response"], "after": experiment["after"]}), flush=True)
            await asyncio.sleep(1.5)

if __name__ == "__main__":
    asyncio.run(main())
