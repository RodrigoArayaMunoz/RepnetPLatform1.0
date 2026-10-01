import test from "node:test";
import assert from "node:assert/strict";
import {
  formatChileClock,
  formatChileDateTime,
  getDeadlineStatus,
  isPendingReturn,
} from "../src/frontend/utils/returnsProjection.js";

test("classifies Mercado Libre deadlines in Chile local time", () => {
  const now = new Date("2026-10-01T12:00:00Z");
  assert.equal(getDeadlineStatus("2026-09-30T19:00:00-03:00", now).key, "overdue");
  assert.equal(getDeadlineStatus("2026-10-01T14:30:00-03:00", now).key, "today");
  assert.equal(getDeadlineStatus("2026-10-02T10:00:00-03:00", now).key, "tomorrow");
  assert.equal(getDeadlineStatus("2026-10-03T10:00:00-03:00", now).key, "later");
  assert.equal(getDeadlineStatus(null, now).key, "unknown");
});

test("formats a deadline with its Chilean time", () => {
  assert.equal(formatChileDateTime("2026-10-01T14:30:00-03:00"), "01/10/2026 - 14:30");
  assert.equal(formatChileDateTime(null), "Pendiente de Mercado Libre");
});

test("formats the live clock with seconds in Chilean time", () => {
  assert.equal(formatChileClock(new Date("2026-10-01T03:00:44Z")), "01/10/2026 - 00:00:44");
  assert.equal(formatChileClock(new Date("2026-10-01T03:00:45Z")), "01/10/2026 - 00:00:45");
});

test("excludes resolved returns from pending counter", () => {
  assert.equal(isPendingReturn({ estado: "Finalizado" }), false);
  assert.equal(isPendingReturn({ estado: "Pendiente" }), true);
  assert.equal(isPendingReturn({ estado: null }), true);
});
