import assert from "node:assert/strict";
import test from "node:test";
import { readRefaxExport } from "../src/lib/refaxExportStream.js";

const type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const file = { type: "file", filename: "productos.xlsx", content_type: type, size: 4 };
const chunk = { type: "chunk", data: btoa("PKab") };
const complete = { type: "complete" };
const responseFor = (events) => new Response(events.map(e => JSON.stringify(e)).join("\n") + "\n");

test("reads real phase progress across split JSON lines and UTF-8 characters", async () => {
  const events = [
    { type: "progress", percentage: 5, stage: "requesting", message: "Conexión verificada…" },
    { type: "heartbeat" },
    { type: "progress", percentage: 74, stage: "building", message: "Generando Excel" },
    file, chunk, complete,
  ];
  const encoded = new TextEncoder().encode(events.map(e => JSON.stringify(e)).join("\n") + "\n");
  const response = new Response(new ReadableStream({
    start(controller) {
      for (let i = 0; i < encoded.length; i += 7) controller.enqueue(encoded.slice(i, i + 7));
      controller.close();
    },
  }));
  const updates = [];
  const result = await readRefaxExport(response, { onProgress: e => updates.push(e) });
  assert.deepEqual(updates.map(e => e.percentage), [5, 74, 99, 100]);
  assert.equal(updates[0].message, "Conexión verificada…");
  assert.equal(await result.blob.text(), "PKab");
  assert.equal(result.filename, "productos.xlsx");
  assert.equal(result.blob.type, type);
});

test("failed or truncated exports never produce a downloadable file or 100%", async () => {
  for (const events of [
    [{ type: "error", message: "REFAX no disponible" }],
    [file, chunk],
    [file, { type: "chunk", data: btoa("PK") }, complete],
  ]) {
    const updates = [];
    await assert.rejects(readRefaxExport(responseFor(events), { onProgress: e => updates.push(e) }));
    assert.ok(updates.every(e => e.percentage < 100));
  }
});

test("percentage does not move backwards on retry and abort stops the stream", async () => {
  const values = [];
  await readRefaxExport(responseFor([
    { type: "progress", percentage: 25, stage: "receiving" },
    { type: "progress", percentage: 5, stage: "retrying" }, file, chunk, complete,
  ]), { onProgress: e => values.push(e.percentage) });
  assert.deepEqual(values, [25, 25, 99, 100]);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(readRefaxExport(responseFor([file, chunk, complete]), {
    signal: controller.signal,
  }), { name: "AbortError" });
});
