import test from "node:test";
import assert from "node:assert/strict";
import {
  expandValidatedSkuCounts,
  findSaleItemBySku,
  getSaleSkuRequirements,
  isSaleSkuValidationComplete,
  normalizeSkuCode,
} from "../src/frontend/utils/skuValidation.js";

test("normaliza espacios y mayúsculas al validar SKU", () => {
  assert.equal(normalizeSkuCode("  sku-123  "), "SKU-123");
});

test("encuentra un SKU perteneciente a la venta", () => {
  const item = findSaleItemBySku(
    [{ sku: "ABC-10", title: "Producto" }],
    "abc-10"
  );
  assert.equal(item?.title, "Producto");
});

test("rechaza códigos que no pertenecen a la venta", () => {
  const item = findSaleItemBySku([{ sku: "ABC-10" }], "XYZ-99");
  assert.equal(item, null);
});

test("suma las cantidades requeridas cuando un SKU se repite", () => {
  assert.deepEqual(
    getSaleSkuRequirements([
      { sku: "SKU-A", quantity: 2 },
      { sku: "sku-a", quantity: 1 },
      { sku: "SKU-B", quantity: 1 },
    ]),
    { "SKU-A": 3, "SKU-B": 1 }
  );
});

test("solo completa la venta al validar todos los SKU y cantidades", () => {
  const items = [
    { sku: "SKU-A", quantity: 2 },
    { sku: "SKU-B", quantity: 1 },
  ];

  assert.equal(
    isSaleSkuValidationComplete(items, { "SKU-A": 1, "SKU-B": 1 }),
    false
  );
  assert.equal(
    isSaleSkuValidationComplete(items, { "SKU-A": 2, "SKU-B": 1 }),
    true
  );
});

test("expande los conteos para validarlos en el backend", () => {
  assert.deepEqual(expandValidatedSkuCounts({ "SKU-A": 2, "SKU-B": 1 }), [
    "SKU-A",
    "SKU-A",
    "SKU-B",
  ]);
});
