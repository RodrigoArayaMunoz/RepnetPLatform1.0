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

const getItemQuantity = (item) => {
  const quantity = Number(item?.quantity);
  return Number.isFinite(quantity) && quantity > 0 ? Math.trunc(quantity) : 1;
};

export const getSaleSkuRequirements = (items) =>
  (Array.isArray(items) ? items : []).reduce((requirements, item) => {
    const sku = normalizeSkuCode(item?.sku);
    if (!sku) return requirements;

    requirements[sku] = (requirements[sku] || 0) + getItemQuantity(item);
    return requirements;
  }, {});

export const isSaleSkuValidationComplete = (
  items,
  validatedSkuCounts = {}
) => {
  const requirements = getSaleSkuRequirements(items);
  const requiredSkus = Object.keys(requirements);
  if (!requiredSkus.length) return false;

  return requiredSkus.every(
    (sku) => Number(validatedSkuCounts[sku] || 0) >= requirements[sku]
  );
};

export const expandValidatedSkuCounts = (validatedSkuCounts = {}) =>
  Object.entries(validatedSkuCounts).flatMap(([sku, count]) =>
    Array.from({ length: Math.max(0, Number(count) || 0) }, () => sku)
  );
