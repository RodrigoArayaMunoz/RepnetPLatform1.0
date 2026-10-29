import test from "node:test";
import assert from "node:assert/strict";
import { createVisiblePoller, pollingError, waitForVisibleJob } from "../src/frontend/utils/visiblePolling.js";

function harness(run, extra = {}, create = createVisiblePoller) {
  const visibility = new EventTarget();
  visibility.visibilityState = "visible";
  let clock = 0;
  let id = 0;
  const pending = new Map();
  const errors = [];
  const stop = create({
    run, visibility, intervalMs: 5000, now: () => clock,
    schedule: (fn, delay) => { pending.set(++id, { fn, delay }); return id; },
    cancel: (key) => pending.delete(key), onError: (error) => errors.push(error), ...extra,
  });
  return {
    errors, pending, stop,
    delay: () => pending.values().next().value?.delay,
    next() {
      const [key, entry] = pending.entries().next().value;
      pending.delete(key);
      clock += entry.delay;
      return entry.fn();
    },
    show(value) {
      visibility.visibilityState = value ? "visible" : "hidden";
      visibility.dispatchEvent(new Event("visibilitychange"));
    },
  };
}

test("slow requests never overlap and hidden/unmounted views do not poll", async () => {
  let finish;
  let calls = 0;
  const poll = harness(async (signal) => {
    calls++;
    await new Promise((resolve) => { finish = resolve; });
    assert.equal(signal.aborted, true);
  });
  const active = poll.next();
  assert.equal(calls, 1);
  assert.equal(poll.pending.size, 0);
  poll.show(false);
  finish();
  await active;
  assert.equal(poll.pending.size, 0);
  poll.show(true);
  assert.equal(poll.delay(), 0);
  poll.stop();
  assert.equal(poll.pending.size, 0);
  poll.show(false);
  poll.show(true);
  assert.equal(poll.pending.size, 0);
});

test("revoked sessions, quota restrictions and removed endpoints stop after one request", async () => {
  for (const status of [400, 401, 402, 403, 404, 410, 422]) {
    const poll = harness(async () => { throw pollingError(new Response(null, { status }), "failed"); });
    await poll.next();
    assert.equal(poll.errors.length, 1);
    assert.equal(poll.errors[0].pollingStopped, true);
    poll.show(false);
    poll.show(true);
    assert.equal(poll.pending.size, 0);
    poll.stop();
  }
});

test("transient failures back off and stop after five attempts", async () => {
  const poll = harness(async () => { throw new Error("network unavailable"); });
  for (const expectedDelay of [0, 10000, 20000, 40000, 60000]) {
    assert.equal(poll.delay(), expectedDelay);
    await poll.next();
  }
  assert.equal(poll.errors.length, 5);
  assert.equal(poll.pending.size, 0);
  poll.stop();
});

test("Retry-After survives visibility changes and success resets backoff", async () => {
  let calls = 0;
  const poll = harness(async () => {
    if (++calls === 1) throw pollingError(new Response(null, { status: 429, headers: { "Retry-After": "90" } }), "wait");
    return calls === 3 ? false : 3000;
  });
  await poll.next();
  assert.equal(poll.delay(), 90000);
  poll.show(false);
  poll.show(true);
  assert.equal(poll.delay(), 90000);
  await poll.next();
  assert.equal(poll.delay(), 3000);
  await poll.next();
  assert.equal(poll.pending.size, 0);
  poll.stop();
});

test("import observation pauses in hidden tabs and resolves once without resubmitting a job", async () => {
  let outcome;
  const states = [];
  const poll = harness(async () => ({ status: states.length ? "success" : "processing" }), {
    onProgress: (data) => states.push(data.status),
  }, (options) => {
    outcome = waitForVisibleJob({ ...options, read: options.run });
    return () => {};
  });
  await poll.next();
  assert.equal(poll.delay(), 5000);
  poll.show(false);
  assert.equal(poll.pending.size, 0);
  poll.show(true);
  await poll.next();
  assert.equal((await outcome).status, "success");
  assert.deepEqual(states, ["processing", "success"]);
  assert.equal(poll.pending.size, 0);
});

test("import observation stops on cancellation or a terminal job failure", async () => {
  for (const cancelObservation of [true, false]) {
    const controller = new AbortController();
    let outcome;
    let calls = 0;
    const poll = harness(async () => { calls++; return { status: "error", message: "Import rejected" }; }, {
      signal: controller.signal, onProgress: () => {},
    }, (options) => {
      outcome = waitForVisibleJob({ ...options, read: options.run });
      return () => controller.abort();
    });
    const rejection = assert.rejects(outcome, cancelObservation ? { name: "AbortError" } : { message: "Import rejected" });
    if (cancelObservation) controller.abort();
    else await poll.next();
    await rejection;
    assert.equal(calls, cancelObservation ? 0 : 1);
    assert.equal(poll.pending.size, 0);
    poll.show(false);
    poll.show(true);
    assert.equal(poll.pending.size, 0);
  }
});
