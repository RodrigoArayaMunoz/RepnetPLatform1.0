import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeStorageFileName } from "./storageFileName.js";

test("removes accents and keeps a valid Excel extension", () => {
  assert.equal(
    sanitizeStorageFileName("NOENCONTRADOS_kitembragueaño.xlsx"),
    "NOENCONTRADOS_kitembragueano.xlsx"
  );
});

test("replaces spaces and unsupported storage-key characters", () => {
  assert.equal(
    sanitizeStorageFileName("precios julio #1 (revisión).xlsx"),
    "precios_julio_1_revision_.xlsx"
  );
});

test("uses a safe fallback for an empty or unsupported name", () => {
  assert.equal(sanitizeStorageFileName(""), "archivo.xlsx");
  assert.equal(sanitizeStorageFileName("ñá?.-"), "na");
  assert.equal(sanitizeStorageFileName("測試"), "archivo.xlsx");
});
