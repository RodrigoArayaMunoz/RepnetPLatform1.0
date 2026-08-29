import test from "node:test";
import assert from "node:assert/strict";
import {
  findSaleItemBySku,
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
