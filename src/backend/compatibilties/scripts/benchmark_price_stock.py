"""Bounded live /items PUT benchmark. Each item is sent at most once.

Run with platform credentials and --execute to apply spreadsheet targets.
Reports contain business values and responses, never access or refresh tokens.
The first 429, timeout, authentication error or server error stops new writes.
"""

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from config import settings
from services.supabase_meli_connection_store import supabase_meli_connection_store


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


async def benchmark(args: argparse.Namespace) -> None:
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Input must contain parsed ML rows.")
    ids = [row["item_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate item IDs are not permitted in a live benchmark.")
    for row in rows:
        if row.get("estado") not in {"active", "paused"}:
            raise ValueError("Benchmark only permits active/paused targets.")
        if row.get("precio") is None or row.get("stock") is None:
            raise ValueError("Missing price or stock target.")
    if not args.execute:
        print(json.dumps({"dry_run": True, "rows": len(rows), "rpm_stages": args.rpm}))
        return

    account_rows = await supabase_meli_connection_store.list_rows(include_tokens=True)
    accounts = [row for row in account_rows if row.get("is_active") and row.get("ml_user_id")]
    if len(accounts) != 1:
        raise RuntimeError("Exactly one connected ML account is required.")
    account = accounts[0]
    expires_at = supabase_meli_connection_store._parse_expires_at(account.get("expires_at"))
    if expires_at < time.time() + len(args.rpm) * args.stage_seconds + 300:
        raise RuntimeError("Token lifetime is insufficient; refresh through the platform first.")
    seller_id = str(account["ml_user_id"])
    token = account.get("access_token")
    if not token:
        raise RuntimeError("Connected account has no token.")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    event_path = output / "requests.jsonl"
    summary_path = output / "summary.json"
    if event_path.exists():
        raise RuntimeError("Output already exists; use a new report directory.")
    stop = asyncio.Event()
    cursor = 0
    summaries: list[dict[str, Any]] = []
    started_at = datetime.now(UTC).isoformat()
    request_sequence = 0

    with event_path.open("w", encoding="utf-8") as events:
        def record(event: dict[str, Any]) -> None:
            events.write(json.dumps(event, ensure_ascii=False) + "\n")
            events.flush()

        def save_summary() -> None:
            summary_path.write_text(json.dumps({
                "started_at": started_at,
                "updated_at": datetime.now(UTC).isoformat(),
                "seller_id": seller_id,
                "rows_in_file": len(rows),
                "rows_inspected": cursor,
                "writes_attempted": request_sequence,
                "stop_triggered": stop.is_set(),
                "stages": summaries,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        timeout = httpx.Timeout(args.timeout, connect=10.0, read=args.timeout, write=30.0, pool=10.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        ) as client:
            identity = await client.get(f"{settings.ml_api_base}/users/me")
            if identity.status_code != 200 or str(identity.json().get("id")) != seller_id:
                raise RuntimeError("Connected token identity could not be verified.")

            for rpm in args.rpm:
                if stop.is_set() or cursor >= len(rows):
                    break
                count = min(math.ceil(rpm * args.stage_seconds / 60), len(rows) - cursor)
                eligible = []
                before_by_id = {}
                while len(eligible) < count and cursor < len(rows):
                    # Keep inspecting until the stage has enough writable rows
                    # to exercise the full observation window, despite skips.
                    selected = rows[cursor:cursor + min(20, count - len(eligible))]
                    cursor += len(selected)
                    response = await client.get(f"{settings.ml_api_base}/items", params={
                        "ids": ",".join(row["item_id"] for row in selected),
                        "attributes": "id,seller_id,price,available_quantity,status,variations,shipping",
                    })
                    if response.status_code != 200:
                        record({"kind": "preflight_error", "rpm": rpm, "http_status": response.status_code,
                                "body": response.text[:1000]})
                        stop.set()
                        break
                    for result in response.json():
                        body = result.get("body") if isinstance(result, dict) else None
                        if result.get("code") == 200 and isinstance(body, dict):
                            before_by_id[body["id"]] = body
                    for row in selected:
                        before = before_by_id.get(row["item_id"], {})
                        if (str(before.get("seller_id")) != seller_id
                                or before.get("status") not in {"active", "paused"}
                                or before.get("variations")
                                or (before.get("shipping") or {}).get("logistic_type") == "fulfillment"):
                            record({"kind": "skipped", "rpm": rpm, "item_id": row["item_id"],
                                    "reason": "owner/state/variations/logistics preflight"})
                            continue
                        eligible.append(row)
                    await asyncio.sleep(0.25)
                if stop.is_set():
                    save_summary()
                    break
                concurrency = args.concurrency or min(20, max(2, math.ceil(rpm / 60 * 2)))
                semaphore = asyncio.Semaphore(concurrency)
                latencies = []
                statuses: Counter[str] = Counter()
                mismatch_count = 0
                warning_count = 0
                consecutive_business_errors = 0
                stage_started = time.monotonic()
                next_send_at = stage_started
                tasks: set[asyncio.Task] = set()
                print(json.dumps({"event": "stage_started", "rpm": rpm, "eligible_rows": len(eligible),
                                  "concurrency": concurrency, "pause_between_blocks": 0}), flush=True)

                async def write(row: dict[str, Any]) -> None:
                    nonlocal request_sequence, mismatch_count, warning_count, consecutive_business_errors
                    try:
                        if stop.is_set():
                            return
                        request_sequence += 1
                        payload = {"price": int(row["precio"]), "available_quantity": int(row["stock"]),
                                   "status": row["estado"]}
                        began = time.monotonic()
                        event: dict[str, Any] = {
                            "kind": "write", "rpm": rpm, "request_sequence": request_sequence,
                            "sent_at": datetime.now(UTC).isoformat(), "item_id": row["item_id"],
                            "excel_row": row["original_row_index"] + 2, "payload": payload,
                            "before": {k: before_by_id[row["item_id"]].get(k) for k in payload},
                        }
                        try:
                            response = await client.put(f"{settings.ml_api_base}/items/{row['item_id']}", json=payload)
                            event["http_status"] = response.status_code
                            event["rate_headers"] = {k: v for k, v in response.headers.items()
                                                     if "rate" in k.lower() or k.lower() == "retry-after"}
                            try:
                                body = response.json()
                            except ValueError:
                                body = {"message": response.text[:1000]}
                            if response.status_code < 300 and isinstance(body, dict):
                                consecutive_business_errors = 0
                                event["result_fields"] = {k: body.get(k) for k in payload}
                                event["warnings"] = body.get("warnings") or []
                                event["mismatched_fields"] = [k for k, v in payload.items() if body.get(k) != v]
                                mismatch_count += bool(event["mismatched_fields"])
                                warning_count += bool(event["warnings"])
                            else:
                                event["error"] = body
                                if response.status_code in {429, 401, 403} or response.status_code >= 500:
                                    stop.set()
                                elif response.status_code >= 400:
                                    consecutive_business_errors += 1
                                    if consecutive_business_errors >= 5:
                                        stop.set()
                            statuses[str(response.status_code)] += 1
                        except httpx.TimeoutException as exc:
                            event["exception"] = type(exc).__name__
                            statuses[type(exc).__name__] += 1
                            stop.set()
                        except httpx.TransportError as exc:
                            event["exception"] = type(exc).__name__
                            statuses[type(exc).__name__] += 1
                            stop.set()
                        elapsed = time.monotonic() - began
                        latencies.append(elapsed)
                        event["latency_seconds"] = round(elapsed, 4)
                        record(event)
                        if sum(statuses.values()) % 25 == 0 or stop.is_set():
                            print(json.dumps({"event": "progress", "rpm": rpm,
                                              "completed": sum(statuses.values()), "statuses": dict(statuses),
                                              "stop": stop.is_set()}), flush=True)
                    finally:
                        semaphore.release()

                for row in eligible:
                    if stop.is_set():
                        break
                    await semaphore.acquire()
                    delay = next_send_at - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if stop.is_set():
                        semaphore.release()
                        break
                    next_send_at = time.monotonic() + 60 / rpm
                    task = asyncio.create_task(write(row))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                if tasks:
                    await asyncio.gather(*tasks)
                elapsed = time.monotonic() - stage_started
                completed = sum(statuses.values())
                summary = {
                    "target_rpm": rpm, "concurrency": concurrency, "eligible_rows": len(eligible),
                    "completed_requests": completed, "statuses": dict(statuses),
                    "duration_seconds": round(elapsed, 3),
                    "observed_rpm": round(completed / elapsed * 60, 2) if elapsed else 0,
                    "latency_mean_seconds": round(statistics.mean(latencies), 4) if latencies else 0,
                    "latency_p95_seconds": round(percentile(latencies, 0.95), 4),
                    "latency_max_seconds": round(max(latencies, default=0), 4),
                    "response_mismatches": mismatch_count, "responses_with_warnings": warning_count,
                }
                summaries.append(summary)
                save_summary()
                print(json.dumps({"event": "stage_finished", **summary}), flush=True)
            save_summary()
    print(json.dumps({"event": "finished", "summary_path": str(summary_path),
                      "writes_attempted": request_sequence, "stop_triggered": stop.is_set()}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rpm", type=int, nargs="+", default=[100, 150, 200, 300, 450, 600, 900, 1200])
    parser.add_argument("--stage-seconds", type=int, default=72)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()
    if any(rpm <= 0 or rpm > 1200 for rpm in args.rpm) or args.stage_seconds < 60:
        parser.error("Stages require 60+ seconds and an explicit rate from 1 to 1200 RPM.")
    if args.concurrency is not None and not 1 <= args.concurrency <= 20:
        parser.error("Concurrency must be from 1 to 20.")
    asyncio.run(benchmark(args))


if __name__ == "__main__":
    main()
