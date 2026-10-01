import assert from "node:assert/strict";
import test from "node:test";
import { readDownloadBlob } from "../src/lib/downloadBlob.js";

test("reports received bytes and reaches 100 only after the complete file", async () => {
  const encoder = new TextEncoder();
  const response = new Response(new ReadableStream({
    start(controller) {
      for (const text of ["ab", "cdef", "gh"]) controller.enqueue(encoder.encode(text));
      controller.close();
    },
  }), { headers: { "Content-Length": "8", "Content-Type": "application/octet-stream" } });
  const progress = [];
  const blob = await readDownloadBlob(response, {
    onProgress: (event) => progress.push(event.percentage),
  });
  assert.equal(await blob.text(), "abcdefgh");
  assert.deepEqual(progress, [0, 25, 75, 99, 100]);
});

test("does not invent a percentage when the total size is unavailable", async () => {
  const progress = [];
  const blob = await readDownloadBlob(new Response("Excel data"), {
    onProgress: (event) => progress.push(event.percentage),
  });
  assert.equal(await blob.text(), "Excel data");
  assert.equal(progress[0], null);
  assert.equal(progress.at(-1), 100);
  assert.ok(progress.slice(0, -1).every((value) => value === null));
});

test("a broken transfer never reports success or returns a partial file", async () => {
  let chunks = 0;
  const response = new Response(new ReadableStream({
    pull(controller) {
      if (chunks++ === 0) controller.enqueue(new Uint8Array([1, 2]));
      else controller.error(new Error("Transfer interrupted"));
    },
  }), { headers: { "Content-Length": "8" } });
  const progress = [];
  await assert.rejects(readDownloadBlob(response, {
    onProgress: (event) => progress.push(event.percentage),
  }), /Transfer interrupted/);
  assert.ok(!progress.includes(100));
});

test("an aborted download stops without reporting success", async () => {
  const controller = new AbortController();
  controller.abort();
  const progress = [];
  await assert.rejects(readDownloadBlob(new Response("Excel data"), {
    signal: controller.signal,
    onProgress: (event) => progress.push(event),
  }), { name: "AbortError" });
  assert.equal(progress.length, 0);
});
