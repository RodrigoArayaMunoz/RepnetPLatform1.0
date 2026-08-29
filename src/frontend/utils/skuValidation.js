export const normalizeSkuCode = (value) =>
  String(value ?? "").trim().toLocaleUpperCase("es-CL");

export const findSaleItemBySku = (items, scannedCode) => {
  const normalizedScannedCode = normalizeSkuCode(scannedCode);
  if (!normalizedScannedCode || !Array.isArray(items)) return null;

  return (
    items.find(
      (item) =>
        item?.sku && normalizeSkuCode(item.sku) === normalizedScannedCode
    ) || null
  );
};
