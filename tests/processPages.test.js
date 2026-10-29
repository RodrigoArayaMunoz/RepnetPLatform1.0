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
