import { useCallback, useEffect, useRef, useState } from "react";
import { authFetch } from "../../lib/apiClient.js";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export const readPublicationError = (data, fallback) => {
  if (typeof data?.detail === "string") return data.detail;
  if (typeof data?.detail?.message === "string") return data.detail.message;
  if (typeof data?.message === "string") return data.message;
  return fallback;
};

export default function usePublicationExport({
  authUserId,
  scope = "date",
  onRestoreDate,
}) {
  // Preserve the existing date export keys and keep catalog jobs independent.
  const storageBase = scope === "catalog"
    ? "repnet_catalog_export"
    : "repnet_publication_export";
  const jobStorageKey = `${storageBase}_job_id:${authUserId || "anonymous"}`;
  const downloadedStorageKey = `${storageBase}_downloaded_job_id:${authUserId || "anonymous"}`;
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const downloadedRef = useRef("");
  const requestInFlightRef = useRef(false);
  const isExporting = ["queued", "processing", "retrying"].includes(job?.status);

  const downloadFile = useCallback(async (exportJob) => {
    const response = await authFetch(
      `${API_BASE}/publications/export/${exportJob.job_id}/download`,
      { method: "GET", credentials: "include" }
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readPublicationError(data, "No se pudo descargar el Excel generado."));
    }

    const blob = await response.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = exportJob.filename || (
      scope === "catalog"
        ? "catalogo_completo_emilia.xlsx"
        : `publicaciones_${exportJob.publication_date}.xlsx`
    );
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(objectUrl);
  }, [scope]);

  const downloadAndRemember = useCallback(async (exportJob) => {
    await downloadFile(exportJob);
    downloadedRef.current = exportJob.job_id;
    window.localStorage.setItem(downloadedStorageKey, exportJob.job_id);
  }, [downloadFile, downloadedStorageKey]);

  const reset = useCallback(() => {
    setJob(null);
    setError("");
    downloadedRef.current = "";
    window.localStorage.removeItem(jobStorageKey);
    window.localStorage.removeItem(downloadedStorageKey);
  }, [downloadedStorageKey, jobStorageKey]);

  useEffect(() => {
    let cancelled = false;
    const restoreJob = async () => {
      // Reset after a user change without deleting their saved job.
      await Promise.resolve();
      if (cancelled) return;
      setJob(null);
      setError("");
      downloadedRef.current = "";
      const storedJobId = window.localStorage.getItem(jobStorageKey);
      if (!storedJobId) return;
      downloadedRef.current = window.localStorage.getItem(downloadedStorageKey) || "";
      try {
        const response = await authFetch(
          `${API_BASE}/publications/export/${storedJobId}`,
          { method: "GET", credentials: "include" }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (response.status === 404) {
            window.localStorage.removeItem(jobStorageKey);
            window.localStorage.removeItem(downloadedStorageKey);
          }
          throw new Error(readPublicationError(data, "No se pudo recuperar la exportación en curso."));
        }
        if ((data.export_scope || "date") !== scope) {
          throw new Error("La exportación guardada corresponde a otra descarga.");
        }
        if (!cancelled) {
          setJob(data);
          if (scope === "date" && data.publication_date) {
            onRestoreDate?.(data.publication_date);
          }
        }
      } catch (restoreError) {
        if (!cancelled) setError(restoreError?.message || "No se pudo recuperar la exportación en curso.");
      }
    };
    restoreJob();
    return () => { cancelled = true; };
  }, [downloadedStorageKey, jobStorageKey, onRestoreDate, scope]);

  useEffect(() => {
    const jobId = job?.job_id;
    if (!jobId || !isExporting) return undefined;
    let cancelled = false;
    let loading = false;
    const loadStatus = async () => {
      if (loading) return;
      loading = true;
      try {
        const response = await authFetch(
          `${API_BASE}/publications/export/${jobId}`,
          { method: "GET", credentials: "include" }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(readPublicationError(data, "No se pudo consultar el estado de la exportación."));
        }
        if (!cancelled) {
          setJob(data);
          if (data.status !== "error") setError("");
        }
      } catch (statusError) {
        if (!cancelled) setError(statusError?.message || "No se pudo consultar el estado de la exportación.");
      } finally {
        loading = false;
      }
    };
    loadStatus();
    const interval = window.setInterval(loadStatus, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [job?.job_id, isExporting]);

  useEffect(() => {
    if (!job?.download_ready || downloadedRef.current === job.job_id) return;
    downloadedRef.current = job.job_id;
    downloadAndRemember(job).catch((downloadError) => {
      downloadedRef.current = "";
      setError(downloadError?.message || "No se pudo descargar el Excel generado.");
    });
  }, [downloadAndRemember, job]);

  const start = async (publicationDate) => {
    if (isStarting || isExporting || requestInFlightRef.current || (scope === "date" && !publicationDate)) return;
    requestInFlightRef.current = true;
    setIsStarting(true);
    setError("");
    try {
      if (job?.download_ready && scope === "date") {
        await downloadAndRemember(job);
        return;
      }
      setJob(null);
      downloadedRef.current = "";
      window.localStorage.removeItem(jobStorageKey);
      window.localStorage.removeItem(downloadedStorageKey);
      const response = await authFetch(`${API_BASE}/publications/export`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scope === "catalog"
          ? { export_scope: "catalog", refresh: true }
          : { publication_date: publicationDate, refresh: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(readPublicationError(data, "No se pudo iniciar la exportación de publicaciones."));
      }
      window.localStorage.setItem(jobStorageKey, data.job_id);
      setJob(data);
    } catch (startError) {
      setError(startError?.message || "No se pudo iniciar la exportación de publicaciones.");
    } finally {
      requestInFlightRef.current = false;
      setIsStarting(false);
    }
  };

  return { job, error, isBusy: isStarting || isExporting, start, reset };
}
