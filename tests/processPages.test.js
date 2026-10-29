import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "@supabase/supabase-js";
import { fetchProcessPage, processQueueRevision } from "../src/frontend/utils/processPages.js";

function database(respond) {
  const calls = [];
  const client = createClient("https://database.example.test", "user-anon-key", {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    global: { fetch: async (input, init) => {
      const url = new URL(input);
      calls.push(url);
      assert.equal(init.headers.get("apikey"), "user-anon-key");
      assert.ok(init.headers.get("Prefer").includes("count=exact"));
      const [rows, total] = respond(url.searchParams);
      return new Response(JSON.stringify(rows), { headers: { "Content-Type": "application/json", "Content-Range": `0-2/${total}` } });
    } },
  });
  return { client, calls };
}

test("large completed histories use a bounded server page and exact total", async () => {
  const { client, calls } = database((params) => {
    assert.equal(params.get("order"), "fecha_proceso.desc,hora_proceso.desc,id.desc");
    assert.equal(params.get("offset"), "6");
    assert.equal(params.get("limit"), "3");
    assert.equal(params.get("id"), "neq.current");
    assert.match(params.get("or"), /estado.is.null/);
    assert.equal(params.get("select").includes("storage_path"), false);
    return [[{ id: 7 }, { id: 8 }, { id: 9 }], 10001];
  });
  const result = await fetchProcessPage(client, { lane: "completed", page: 3, currentId: "current" });
  assert.equal(calls.length, 1);
  assert.equal(result.total, 10001);
  assert.equal(result.rows.length, 3);
});

test("pending pages keep FIFO positions when new files arrive, including timestamp ties", async () => {
  const rows = [
    { id: "z-first", proceso_id: "00001", fecha_proceso: "2026-10-28", hora_proceso: "23:59:00" },
    { id: "y-second", proceso_id: "00002", fecha_proceso: "2026-10-29", hora_proceso: "00:00:00" },
    { id: "x-third", proceso_id: "00003", fecha_proceso: "2026-10-29", hora_proceso: "00:01:00" },
    { id: "a-fourth", proceso_id: "00004", fecha_proceso: "2026-10-29", hora_proceso: "00:01:00" },
  ];
  const { client } = database((params) => {
    const ordered = [...rows];
    for (const term of params.get("order").split(",").reverse()) {
      const [field, direction] = term.split(".");
      ordered.sort((a, b) => a[field].localeCompare(b[field]) * (direction === "asc" ? 1 : -1));
    }
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    return [ordered.slice(offset, offset + limit), ordered.length];
  });
  const first = await fetchProcessPage(client, { lane: "pending" });
  assert.deepEqual(first.rows.map((row) => row.id), ["z-first", "y-second", "x-third"]);
  rows.unshift({ id: "b-new", proceso_id: "00005", fecha_proceso: "2026-10-29", hora_proceso: "00:02:00" });
  const second = await fetchProcessPage(client, { lane: "pending", page: 2 });
  assert.deepEqual(second.rows.map((row) => row.id), ["a-fourth", "b-new"]);
  assert.equal(second.total, 5);
});

test("current file is first in processing, excluded from pending and not duplicated across pages", async () => {
  const { client } = database((params) => {
    if (params.get("id") === "eq.current") return [[{ id: "current", estado: "Pendiente" }], 1];
    assert.equal(params.get("id"), "neq.current");
    if (params.get("estado") === "ilike.Pendiente") return [[{ id: "pending" }], 2];
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    return [Array.from({ length: limit }, (_, i) => ({ id: offset + i })), 10];
  });
  const first = await fetchProcessPage(client, { lane: "processing", currentId: "current" });
  const second = await fetchProcessPage(client, { lane: "processing", currentId: "current", page: 2 });
  const pending = await fetchProcessPage(client, { lane: "pending", currentId: "current" });
  assert.deepEqual(first.rows.map((row) => row.id), ["current", 0, 1]);
  assert.deepEqual(second.rows.map((row) => row.id), [2, 3, 4]);
  assert.equal(first.total, 11);
  assert.deepEqual(pending.rows.map((row) => row.id), ["pending"]);
});

test("progress updates do not trigger history reloads, job transitions do", () => {
  const state = { running: true, task_id: "queue", current_process_row_id: "a", processed_count: 1 };
  const revision = processQueueRevision(state);
  for (let i = 0; i < 100; i++) {
    assert.equal(processQueueRevision({ ...state, job_processed_rows: i, job_message: String(i) }), revision);
  }
  assert.notEqual(processQueueRevision({ ...state, current_process_row_id: "b" }), revision);
  assert.notEqual(processQueueRevision({ ...state, running: false, completed_at: 99 }), revision);
});

test("database failures are propagated with HTTP status to halt or back off polling", async () => {
  const client = createClient("https://database.example.test", "user-anon-key", {
    auth: { persistSession: false },
    global: { fetch: async () => new Response(JSON.stringify({ message: "denied" }), { status: 403 }) },
  });
  await assert.rejects(fetchProcessPage(client, { lane: "pending" }), { status: 403 });
});

test("deleting rows from an open last page recovers the count without fetching the history", async () => {
  const offsets = [];
  const client = createClient("https://database.example.test", "user-anon-key", {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch: async (input) => {
      const params = new URL(input).searchParams;
      offsets.push(params.get("offset"));
      if (offsets.length === 1) {
        return new Response(JSON.stringify({ code: "PGRST103", message: "Range not satisfiable" }), { status: 416 });
      }
      assert.equal(params.get("limit"), "1");
      return new Response(JSON.stringify([{ id: "remaining" }]), { headers: { "Content-Range": "0-0/4" } });
    } },
  });
  const result = await fetchProcessPage(client, { lane: "pending", page: 3 });
  assert.deepEqual(offsets, ["6", "0"]);
  assert.equal(result.total, 4);
  assert.deepEqual(result.rows, []);
});
