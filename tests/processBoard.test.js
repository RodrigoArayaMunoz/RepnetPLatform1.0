import test from "node:test";
import assert from "node:assert/strict";
import {
  getProcessStatusTone,
  groupProcessRows,
  isCurrentProcess,
  PROCESS_STATUS,
} from "../src/frontend/utils/processBoard.js";

test("groups pending, running and every terminal result into the corresponding lane", () => {
  const rows = [
    { id: 6, estado: PROCESS_STATUS.PENDING },
    { id: 5, estado: PROCESS_STATUS.PROCESSING },
    { id: 4, estado: PROCESS_STATUS.PROCESSED },
    { id: 3, estado: PROCESS_STATUS.PROCESSED_WITH_ERRORS },
    { id: 2, estado: PROCESS_STATUS.ERROR },
    { id: 1, estado: "Completado" },
  ];
  const groups = groupProcessRows(rows, false, null);

  assert.deepEqual(groups.processing.map((row) => row.id), [5]);
  assert.deepEqual(groups.pending.map((row) => row.id), [6]);
  assert.deepEqual(groups.completed.map((row) => row.id), [4, 3, 2, 1]);
  assert.equal(Object.values(groups).flat().length, rows.length);
});

test("queue status moves the current pending record into execution despite different ID types", () => {
  const rows = [
    { id: 3, estado: "Procesando" },
    { id: 2, estado: "Pendiente" },
    { id: 1, estado: "Pendiente" },
  ];
  const groups = groupProcessRows(rows, true, "2");

  assert.deepEqual(groups.processing.map((row) => row.id), [2, 3]);
  assert.deepEqual(groups.pending.map((row) => row.id), [1]);
  assert.equal(rows[1].estado, "Pendiente");
  assert.equal(isCurrentProcess(rows[1], false, "2"), false);
  assert.equal(isCurrentProcess({ id: null }, true, null), false);
});

test("reclassifies the same process as execution starts and completes", () => {
  const row = { id: "job-1", estado: "Pendiente" };
  assert.equal(groupProcessRows([row], false, null).pending.length, 1);
  assert.equal(groupProcessRows([row], true, "job-1").processing.length, 1);

  const completed = { ...row, estado: "Procesado", displayEstado: "Procesado con Errores" };
  const groups = groupProcessRows([completed], false, null);
  assert.equal(groups.processing.length, 0);
  assert.equal(groups.pending.length, 0);
  assert.deepEqual(groups.completed, [completed]);
});

test("preserves the queue worker's newest-first order and groups using the persisted status", () => {
  const rows = [
    { id: 5, estado: " pendiente ", displayEstado: "Error" },
    { id: 4, estado: "Procesado" },
    { id: 3, estado: "PENDIENTE" },
    { id: 2, estado: "Error" },
    { id: 1, estado: "Pendiente" },
  ];
  const original = structuredClone(rows);
  const groups = groupProcessRows(rows, false, null);

  assert.deepEqual(groups.pending.map((row) => row.id), [5, 3, 1]);
  assert.deepEqual(groups.completed.map((row) => row.id), [4, 2]);
  assert.deepEqual(rows, original);
});

test("distinguishes successful, partial and failed results", () => {
  assert.equal(getProcessStatusTone("Procesado"), "success");
  assert.equal(getProcessStatusTone("Procesado con Errores"), "partial");
  assert.equal(getProcessStatusTone("Error"), "danger");
  assert.equal(getProcessStatusTone("Procesando"), "info");
  assert.equal(getProcessStatusTone("Pendiente"), "warning");
  assert.equal(getProcessStatusTone(null), "neutral");
});
