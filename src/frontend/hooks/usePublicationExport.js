import { useCallback, useEffect, useRef, useState } from "react";
import { authFetch } from "../../lib/apiClient.js";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export const readPublicationError = (data, fallback) => {
  if (typeof data?.detail === "string") return data.detail;
  if (typeof data?.detail?.message === "string") return data.detail.message;
  if (typeof data?.message === "string") return data.message;
  return fallback;
};

export default function usePublicationExport({ authUserId, scope = "date", visitKey }) {
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
  const requestControllerRef = useRef(null);
  const downloadControllerRef = useRef(null);
  const generationRef = useRef(0);
  const isExporting = ["queued", "processing", "retrying"].includes(job?.status);

  const clearStoredExport = useCallback(() => {
    // Remove references left by older versions; exports now belong to this visit.
    window.localStorage.removeItem(jobStorageKey);
    window.localStorage.removeItem(downloadedStorageKey);
  }, [downloadedStorageKey, jobStorageKey]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    requestControllerRef.current?.abort();
    downloadControllerRef.current?.abort();
    requestControllerRef.current = null;
    requestInFlightRef.current = false;
    downloadedRef.current = "";
    setJob(null);
    setError("");
    setIsStarting(false);
    clearStoredExport();
  }, [clearStoredExport]);

  useEffect(() => {
    let cancelled = false;
    clearStoredExport();
    const initializeVisit = async () => {
      await Promise.resolve();
      if (!cancelled) reset();
    };
    initializeVisit();
    return () => {
      cancelled = true;
      generationRef.current += 1;
      requestControllerRef.current?.abort();
      downloadControllerRef.current?.abort();
      requestControllerRef.current = null;
      requestInFlightRef.current = false;
      clearStoredExport();
    };
  }, [clearStoredExport, reset, visitKey]);

  const downloadFile = useCallback(async (exportJob, signal) => {
    const response = await authFetch(
      `${API_BASE}/publications/export/${exportJob.job_id}/download`,
      { method: "GET", credentials: "include", signal }
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readPublicationError(data, "No se pudo descargar el Excel generado."));
    }

    const blob = await response.blob();
    signal.throwIfAborted();
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

  useEffect(() => {
    const jobId = job?.job_id;
    if (!jobId || !isExporting) return undefined;
    const generation = generationRef.current;
    const controller = new AbortController();
    let loading = false;
    const loadStatus = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        const response = await authFetch(
          `${API_BASE}/publications/export/${jobId}`,
          { method: "GET", credentials: "include", signal: controller.signal }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(readPublicationError(data, "No se pudo consultar el estado de la exportación."));
        }
        if (!controller.signal.aborted && generation === generationRef.current) {
          setJob(data);
          if (data.status !== "error") setError("");
        }
      } catch (statusError) {
        if (!controller.signal.aborted && generation === generationRef.current) {
          setError(statusError?.message || "No se pudo consultar el estado de la exportación.");
        }
      } finally {
        loading = false;
      }
    };
    loadStatus();
    const interval = window.setInterval(loadStatus, 1000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [job?.job_id, isExporting]);

  useEffect(() => {
    if (!job?.download_ready || downloadedRef.current === job.job_id) return undefined;
    const generation = generationRef.current;
    const controller = new AbortController();
    downloadedRef.current = job.job_id;
    downloadControllerRef.current = controller;
    downloadFile(job, controller.signal).catch((downloadError) => {
      if (!controller.signal.aborted && generation === generationRef.current) {
        downloadedRef.current = "";
        setError(downloadError?.message || "No se pudo descargar el Excel generado.");
      }
    });
    return () => controller.abort();
  }, [downloadFile, job]);

  const start = async (publicationDate) => {
    if (isStarting || isExporting || requestInFlightRef.current || (scope === "date" && !publicationDate)) return;
    const generation = generationRef.current;
    const controller = new AbortController();
    requestControllerRef.current = controller;
    requestInFlightRef.current = true;
    setIsStarting(true);
    setError("");
    try {
      if (job?.download_ready && scope === "date") {
        await downloadFile(job, controller.signal);
        return;
      }
      setJob(null);
      downloadedRef.current = "";
      const response = await authFetch(`${API_BASE}/publications/export`, {
        method: "POST",
        credentials: "include",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scope === "catalog"
          ? { export_scope: "catalog", refresh: true }
          : { publication_date: publicationDate, refresh: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(readPublicationError(data, "No se pudo iniciar la exportación de publicaciones."));
      }
      if (!controller.signal.aborted && generation === generationRef.current) setJob(data);
    } catch (startError) {
      if (!controller.signal.aborted && generation === generationRef.current) {
        setError(startError?.message || "No se pudo iniciar la exportación de publicaciones.");
      }
    } finally {
      if (generation === generationRef.current) {
        requestInFlightRef.current = false;
        requestControllerRef.current = null;
        setIsStarting(false);
      }
    }
  };

  return { job, error, isBusy: isStarting || isExporting, start, reset };
}
