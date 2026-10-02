"""Bounded live description benchmark; --execute applies the exact Excel text.

Rows may be repeated with identical text to observe complete rate windows.
No retries: the first 429, timeout, transport/auth/server error stops new sends.
Snapshots and reports contain business data, never tokens. Keep them out of Git.
"""

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from config import settings
from services.item_description_service import _same_text, load_item_description_rows
from services.process_queue_store import process_queue_store
from services.supabase_meli_connection_store import supabase_meli_connection_store


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)] if ordered else 0


class Pacer:
    def __init__(self, rpm):
        self.interval = 60 / rpm
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_at = time.monotonic() + self.interval


async def benchmark(args):
    source = Path(args.file)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = load_item_description_rows(str(source))
    if any(row.get("validation_error") or "duplicate_of" in row for row in rows):
        raise ValueError("Live benchmarks require unique valid rows.")
    if not args.execute:
        print(json.dumps({"dry_run": True, "rows": len(rows), "sha256": digest,
                          "rpm": args.rpm, "mode": args.mode}))
        return
    if process_queue_store.get_state().get("running"):
        raise RuntimeError("A platform process is running; benchmark not started.")
    accounts = [r for r in await supabase_meli_connection_store.list_rows(include_tokens=True)
                if r.get("is_active") and r.get("ml_user_id")]
    if len(accounts) != 1:
        raise RuntimeError("Exactly one active ML account is required.")
    account = accounts[0]
    expiry = supabase_meli_connection_store._parse_expires_at(account.get("expires_at"))
    if expiry < time.time() + len(args.rpm) * args.stage_seconds + len(rows) * .6 + 300:
        raise RuntimeError("Connected token lifetime is insufficient.")
    seller_id = str(account["ml_user_id"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "requests.jsonl").exists():
        raise RuntimeError("Use a fresh output directory.")
    stop = asyncio.Event()
    snapshots = {}
    stages = []
    first_failure = None
    sequence = 0
    started_at = datetime.now(UTC).isoformat()

    def save():
        (output / "summary.json").write_text(json.dumps({
            "started_at": started_at, "updated_at": datetime.now(UTC).isoformat(),
            "seller_id": seller_id, "source_sha256": digest, "unique_rows": len(rows),
            "mode": args.mode, "stop_triggered": stop.is_set(), "first_failure": first_failure,
            "stages": stages,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    with (output / "requests.jsonl").open("w", encoding="utf-8") as events:
        def record(event):
            events.write(json.dumps(event, ensure_ascii=False) + "\n")
            events.flush()

        timeout = httpx.Timeout(args.timeout, connect=10, write=30, pool=10)
        async with httpx.AsyncClient(
            base_url=settings.ml_api_base, timeout=timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
            headers={"Authorization": f"Bearer {account['access_token']}"},
        ) as client:
            identity = await client.get("/users/me")
            if identity.status_code != 200 or str(identity.json().get("id")) != seller_id:
                raise RuntimeError("Token identity mismatch.")
            for offset in range(0, len(rows), 20):
                selected = rows[offset:offset + 20]
                response = await client.get("/items", params={
                    "ids": ",".join(r["item_id"] for r in selected), "attributes": "id,seller_id,status"})
                if response.status_code != 200:
                    raise RuntimeError(f"Ownership preflight HTTP {response.status_code}")
                owned = {r.get("body", {}).get("id") for r in response.json()
                         if r.get("code") == 200 and str(r.get("body", {}).get("seller_id")) == seller_id}
                if owned != {r["item_id"] for r in selected}:
                    raise RuntimeError("Item ownership could not be confirmed.")
                await asyncio.sleep(.25)

            async def request(method, row, pacer, phase, stage_events):
                nonlocal first_failure, sequence
                await pacer.wait()
                if stop.is_set():
                    return None
                if process_queue_store.get_state().get("running"):
                    stop.set()
                    first_failure = first_failure or {"reason": "platform_queue_started"}
                    return None
                sequence += 1
                event = {"sequence": sequence, "phase": phase, "method": method,
                         "item_id": row["item_id"], "excel_row": row["excel_row"],
                         "sent_at": datetime.now(UTC).isoformat()}
                began = time.monotonic()
                body = None
                try:
                    response = await client.request(method, f"/items/{row['item_id']}/description",
                        params={"api_version": "2"} if method == "PUT" else None,
                        json={"plain_text": row["plain_text"]} if method != "GET" else None)
                    event["http_status"] = response.status_code
                    event["rate_headers"] = {k: v for k, v in response.headers.items()
                                             if "rate" in k.lower() or k.lower() == "retry-after"}
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    if not isinstance(body, dict):
                        body = {"unexpected_response_type": type(body).__name__}
                    expected = response.status_code == 404 and method == "GET"
                    if expected and args.mode == "verify" and phase == "stage":
                        event["verified"] = False
                        event["error"] = "description_missing"
                        stop.set()
                    elif response.is_error and not expected:
                        event["error"] = body or response.text[:1000]
                        stop.set()
                    elif method == "GET" and response.status_code == 200:
                        event["read_matches_excel"] = _same_text(body.get("plain_text"), row["plain_text"])
                        if args.mode == "verify" and phase == "stage":
                            event["verified"] = event["read_matches_excel"]
                            if not event["verified"]:
                                event["error"] = "description_mismatch"
                                stop.set()
                    elif method != "GET":
                        event["text_confirmed"] = _same_text(body.get("plain_text"), row["plain_text"])
                        if not event["text_confirmed"]:
                            # Metadata-only success is read back in a separately paced pass.
                            event["needs_readback"] = "plain_text" not in body
                            if not event["needs_readback"]:
                                stop.set()
                                event["error"] = "description_mismatch"
                except httpx.TransportError as exc:
                    event["exception"] = type(exc).__name__
                    stop.set()
                except Exception as exc:
                    event["exception"] = type(exc).__name__
                    stop.set()
                event["latency_seconds"] = round(time.monotonic() - began, 4)
                if stop.is_set() and first_failure is None:
                    first_failure = event.copy()
                stage_events.append(event)
                record(event)
                return event, body

            if args.snapshot:
                previous = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
                if previous["source_sha256"] != digest or str(previous["seller_id"]) != seller_id:
                    raise RuntimeError("Snapshot does not match file/account.")
                snapshots = previous["items"]
            else:
                pacer = Pacer(100)
                for index, row in enumerate(rows, 1):
                    result = await request("GET", row, pacer, "preflight", [])
                    if stop.is_set():
                        save()
                        return
                    event, body = result
                    snapshots[row["item_id"]] = {"exists": event["http_status"] != 404,
                        "plain_text": body.get("plain_text"),
                        "already_matches": _same_text(body.get("plain_text"), row["plain_text"])}
                    if index % 50 == 0:
                        print(json.dumps({"event": "preflight", "read": index, "total": len(rows)}), flush=True)
                (output / "before.json").write_text(json.dumps({"source_sha256": digest,
                    "seller_id": seller_id, "items": snapshots}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"event": "preflight_finished", "rows": len(rows),
                "existing": sum(r["exists"] for r in snapshots.values()),
                "matches_before_initial_writes": sum(r["already_matches"] for r in snapshots.values())}), flush=True)
            for rpm in args.rpm:
                if stop.is_set():
                    break
                pacer = Pacer(rpm)
                semaphore = asyncio.Semaphore(args.concurrency)
                stage_events = []
                count = math.ceil(rpm * args.stage_seconds / 60 / (2 if args.mode == "mixed" else 1))
                began = time.monotonic()
                print(json.dumps({"event": "stage_started", "rpm": rpm, "mode": args.mode,
                                  "rows_scheduled": count, "concurrency": args.concurrency}), flush=True)

                async def process(row):
                    try:
                        if stop.is_set():
                            return
                        exists = snapshots[row["item_id"]]["exists"]
                        if args.mode in {"mixed", "verify"}:
                            result = await request("GET", row, pacer, "stage", stage_events)
                            if result is None or stop.is_set():
                                return
                            event, body = result
                            exists = event["http_status"] != 404
                            if args.mode == "verify":
                                if event["http_status"] == 404:
                                    stop.set()
                                return
                        result = await request("PUT" if exists else "POST", row, pacer, "stage", stage_events)
                        if result and result[0].get("http_status", 999) < 300:
                            snapshots[row["item_id"]]["exists"] = True
                    finally:
                        semaphore.release()

                tasks = set()
                last_progress = began
                for index in range(min(count, len(rows)) if args.mode == "verify" else count):
                    if stop.is_set():
                        break
                    await semaphore.acquire()
                    task = asyncio.create_task(process(rows[index % len(rows)]))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    if time.monotonic() - last_progress > 15:
                        print(json.dumps({"event": "progress", "rpm": rpm,
                                          "requests_completed": len(stage_events)}), flush=True)
                        last_progress = time.monotonic()
                if tasks:
                    await asyncio.gather(*tasks)
                elapsed = time.monotonic() - began
                latencies = [r["latency_seconds"] for r in stage_events]
                summary = {"target_rpm": rpm, "requests": len(stage_events),
                    "writes": sum(r["method"] != "GET" for r in stage_events),
                    "statuses": dict(Counter(str(r.get("http_status", r.get("exception"))) for r in stage_events)),
                    "duration_seconds": round(elapsed, 3),
                    "observed_rpm": round(len(stage_events) / elapsed * 60, 2),
                    "latency_p95_seconds": round(percentile(latencies, .95), 4),
                    "latency_mean_seconds": round(statistics.mean(latencies), 4) if latencies else 0,
                    "latency_max_seconds": max(latencies, default=0),
                    "text_confirmed": sum(r.get("text_confirmed", False) for r in stage_events),
                    "readbacks_needed": sum(r.get("needs_readback", False) for r in stage_events),
                    "verified": sum(r.get("verified", False) for r in stage_events),
                    "unique_items": len({r["item_id"] for r in stage_events})}
                stages.append(summary)
                save()
                print(json.dumps({"event": "stage_finished", **summary}), flush=True)
            (output / "after.json").write_text(json.dumps({"source_sha256": digest,
                "seller_id": seller_id, "items": snapshots}, ensure_ascii=False, indent=2), encoding="utf-8")
            save()
    print(json.dumps({"event": "finished", "summary_path": str(output / "summary.json"),
                      "stop_triggered": stop.is_set()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--mode", choices=["writes", "mixed", "verify"], default="writes")
    parser.add_argument("--rpm", type=int, nargs="+", default=[100, 200, 300, 450, 600, 900, 1200])
    parser.add_argument("--stage-seconds", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if (any(r <= 0 or r > 1200 for r in args.rpm) or not 60 <= args.stage_seconds <= 300
            or len(args.rpm) > 8 or not 1 <= args.concurrency <= 20):
        parser.error("Use 1..1200 RPM, 60..300 second stages, at most 8 stages and concurrency 1..20.")
    asyncio.run(benchmark(args))


if __name__ == "__main__":
    main()
