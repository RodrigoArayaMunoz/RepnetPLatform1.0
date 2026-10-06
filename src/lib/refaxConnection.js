import { authFetch } from "./apiClient.js";
import { readDownloadBlob } from "./downloadBlob.js";
import { readRefaxExport } from "./refaxExportStream.js";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function requestRefax(path, init) {
  const response = await authFetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      data?.detail || data?.message || "No se pudo completar la solicitud a REFAX."
    );
  }
  return data;
}

export function getRefaxStatus() {
  return requestRefax("/refax/status", { method: "GET" });
}

export function connectRefax() {
  return requestRefax("/refax/connect", { method: "POST" });
}

export function getTestRefaxStatus() {
  return requestRefax("/refax/test/status", { method: "GET" });
}

export function connectTestRefax() {
  return requestRefax("/refax/test/connect", { method: "POST" });
}

export function downloadRefaxProducts(options = {}) {
  return downloadProducts("/refax/products/download", options);
}

export function downloadTestRefaxProducts(options = {}) {
  return downloadProducts("/refax/test/products/download", options);
}

async function downloadProducts(path, { onProgress, signal } = {}) {
  onProgress?.({ stage: "preparing", percentage: 0 });
  const response = await authFetch(`${API_BASE}${path}?progress=true`, {
    method: "GET",
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      data?.detail || data?.message || "No se pudieron descargar los productos."
    );
  }

  if (response.headers.get("content-type")?.includes("application/x-ndjson")) {
    return readRefaxExport(response, { onProgress, signal });
  }

  const disposition = response.headers.get("content-disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) {
    throw new Error("El servidor no entregó un archivo Excel válido. Vuelve a intentar la descarga.");
  }
  return {
    blob: await readDownloadBlob(response, { onProgress, signal }),
    filename: filenameMatch?.[1] || "productos_refax.xlsx",
  };
}
