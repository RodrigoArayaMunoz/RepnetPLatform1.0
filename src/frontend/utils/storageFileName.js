const FALLBACK_FILE_NAME = "archivo.xlsx";

export function sanitizeStorageFileName(fileName) {
  const baseName =
    String(fileName ?? "")
      .split(/[\\/]/)
      .pop()
      ?.trim() || FALLBACK_FILE_NAME;

  const safeName = baseName
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[._-]+|[._-]+$/g, "");

  return safeName || FALLBACK_FILE_NAME;
}
