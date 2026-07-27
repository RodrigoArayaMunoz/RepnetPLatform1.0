import { useCallback, useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  CloudDownload,
  Download,
  LoaderCircle,
} from "lucide-react";
import { authFetch } from "../../lib/apiClient.js";
import "../styles/DownloadPublications.css";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const EXPORT_JOB_STORAGE_KEY_BASE = "repnet_publication_export_job_id";
const DOWNLOADED_EXPORT_STORAGE_KEY_BASE =
  "repnet_publication_export_downloaded_job_id";

const getLocalToday = () => {
  const today = new Date();
  const timezoneOffset = today.getTimezoneOffset() * 60_000;

  return new Date(today.getTime() - timezoneOffset)
    .toISOString()
    .slice(0, 10);
};

const readErrorMessage = (data, fallback) => {
  if (typeof data?.detail === "string") {
    return data.detail;
  }
  if (typeof data?.detail?.message === "string") {
    return data.detail.message;
  }
  if (typeof data?.message === "string") {
    return data.message;
  }
  return fallback;
};

const userStorageKey = (baseKey, authUserId) =>
  `${baseKey}:${authUserId || "anonymous"}`;

export default function DownloadPublications({ authUserId }) {
  const exportJobStorageKey = userStorageKey(
    EXPORT_JOB_STORAGE_KEY_BASE,
    authUserId
  );
  const downloadedExportStorageKey = userStorageKey(
    DOWNLOADED_EXPORT_STORAGE_KEY_BASE,
    authUserId
  );
  const [publicationDate, setPublicationDate] = useState(getLocalToday);
  const [syncState, setSyncState] = useState(null);
  const [syncError, setSyncError] = useState("");
  const [isSyncStatusLoaded, setIsSyncStatusLoaded] = useState(false);
  const [exportJob, setExportJob] = useState(null);
  const [exportError, setExportError] = useState("");
  const [isStartingExport, setIsStartingExport] = useState(false);
  const downloadedExportRef = useRef("");
  const exportRequestInFlightRef = useRef(false);
  const isSyncing = Boolean(syncState?.running);
  const isExporting = ["queued", "processing", "retrying"].includes(
    exportJob?.status
  );
  const isExportBusy = isStartingExport || isExporting;
  const hasStoredPublications = syncState?.has_publications === true;
  const showPublicationLoadControls =
    isSyncStatusLoaded && !hasStoredPublications;

  const loadSyncStatus = useCallback(async () => {
    try {
      const response = await authFetch(
        `${API_BASE}/publications/sync/status`,
        {
          method: "GET",
          credentials: "include",
        }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          readErrorMessage(
            data,
            "No se pudo consultar el estado de las publicaciones."
          )
        );
      }

      setSyncState(data);
      setIsSyncStatusLoaded(true);
      if (data?.status !== "error") {
        setSyncError("");
      }
    } catch (error) {
      setSyncError(
        error?.message ||
          "No se pudo consultar el estado de las publicaciones."
      );
    }
  }, []);

  useEffect(() => {
    loadSyncStatus();
  }, [loadSyncStatus]);

  useEffect(() => {
    if (!isSyncing) {
      return undefined;
    }

    const interval = window.setInterval(loadSyncStatus, 3000);
    return () => window.clearInterval(interval);
  }, [isSyncing, loadSyncStatus]);

  const downloadExportFile = useCallback(async (job) => {
    const response = await authFetch(
      `${API_BASE}/publications/export/${job.job_id}/download`,
      {
        method: "GET",
        credentials: "include",
      }
    );

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(
        readErrorMessage(data, "No se pudo descargar el Excel generado.")
      );
    }

    const blob = await response.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download =
      job.filename || `publicaciones_${job.publication_date}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(objectUrl);
  }, []);

  const downloadAndRememberExport = useCallback(
    async (job) => {
      await downloadExportFile(job);
      downloadedExportRef.current = job.job_id;
      window.localStorage.setItem(
        downloadedExportStorageKey,
        job.job_id
      );
    },
    [downloadExportFile, downloadedExportStorageKey]
  );

  useEffect(() => {
    setExportJob(null);
    setExportError("");
    downloadedExportRef.current = "";

    const storedJobId = window.localStorage.getItem(exportJobStorageKey);
    if (!storedJobId) {
      return undefined;
    }

    downloadedExportRef.current =
      window.localStorage.getItem(downloadedExportStorageKey) || "";

    let cancelled = false;
    const restoreExportJob = async () => {
      try {
        const response = await authFetch(
          `${API_BASE}/publications/export/${storedJobId}`,
          {
            method: "GET",
            credentials: "include",
          }
        );
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          if (response.status === 404) {
            window.localStorage.removeItem(exportJobStorageKey);
            window.localStorage.removeItem(downloadedExportStorageKey);
          }
          throw new Error(
            readErrorMessage(
              data,
              "No se pudo recuperar la exportacion en curso."
            )
          );
        }

        if (!cancelled) {
          setExportJob(data);
          if (data?.publication_date) {
            setPublicationDate(data.publication_date);
          }
        }
      } catch (error) {
        if (!cancelled) {
          setExportError(
            error?.message ||
              "No se pudo recuperar la exportacion en curso."
          );
        }
      }
    };

    restoreExportJob();
    return () => {
      cancelled = true;
    };
  }, [downloadedExportStorageKey, exportJobStorageKey]);

  useEffect(() => {
    const jobId = exportJob?.job_id;
    if (!jobId || !isExporting) {
      return undefined;
    }

    let cancelled = false;
    const loadExportStatus = async () => {
      try {
        const response = await authFetch(
          `${API_BASE}/publications/export/${jobId}`,
          {
            method: "GET",
            credentials: "include",
          }
        );
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            readErrorMessage(
              data,
              "No se pudo consultar el estado de la exportacion."
            )
          );
        }

        if (!cancelled) {
          setExportJob(data);
          if (data?.status !== "error") {
            setExportError("");
          }
        }
      } catch (error) {
        if (!cancelled) {
          setExportError(
            error?.message ||
              "No se pudo consultar el estado de la exportacion."
          );
        }
      }
    };

    loadExportStatus();
    const interval = window.setInterval(loadExportStatus, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [exportJob?.job_id, isExporting]);

  useEffect(() => {
    if (
      !exportJob?.download_ready ||
      downloadedExportRef.current === exportJob.job_id
    ) {
      return;
    }

    downloadedExportRef.current = exportJob.job_id;
    downloadAndRememberExport(exportJob).catch((error) => {
      downloadedExportRef.current = "";
      setExportError(
        error?.message || "No se pudo descargar el Excel generado."
      );
    });
  }, [downloadAndRememberExport, exportJob]);

  const handleLoadPublications = async () => {
    if (isSyncing) {
      return;
    }

    try {
      setSyncError("");

      const response = await authFetch(`${API_BASE}/publications/sync`, {
        method: "POST",
        credentials: "include",
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 409 && data?.detail?.state) {
          setSyncState(data.detail.state);
        }
        throw new Error(
          readErrorMessage(
            data,
            "No se pudo iniciar la carga de publicaciones."
          )
        );
      }

      setSyncState(data);
    } catch (error) {
      setSyncError(
        error?.message || "No se pudo iniciar la carga de publicaciones."
      );
    }
  };

  const handleDownloadPublications = async () => {
    if (
      isExportBusy ||
      exportRequestInFlightRef.current ||
      !publicationDate
    ) {
      return;
    }

    if (exportJob?.download_ready) {
      try {
        setExportError("");
        await downloadAndRememberExport(exportJob);
      } catch (error) {
        setExportError(
          error?.message || "No se pudo descargar el Excel generado."
        );
      }
      return;
    }

    try {
      exportRequestInFlightRef.current = true;
      setIsStartingExport(true);
      setExportError("");
      setExportJob(null);
      downloadedExportRef.current = "";
      window.localStorage.removeItem(downloadedExportStorageKey);

      const response = await authFetch(`${API_BASE}/publications/export`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          publication_date: publicationDate,
        }),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          readErrorMessage(
            data,
            "No se pudo iniciar la exportacion de publicaciones."
          )
        );
      }

      window.localStorage.setItem(exportJobStorageKey, data.job_id);
      setExportJob(data);
    } catch (error) {
      setExportError(
        error?.message ||
          "No se pudo iniciar la exportacion de publicaciones."
      );
    } finally {
      exportRequestInFlightRef.current = false;
      setIsStartingExport(false);
    }
  };

  return (
    <section className="download-publications-page">
      <div className="download-publications-layout">
        <header className="download-publications-header">
          <h1>Descargar Publicaciones</h1>
          <p>
            Carga las publicaciones disponibles y descarga la información de la
            fecha seleccionada.
          </p>
        </header>

        <div className="download-publications-card">
          {showPublicationLoadControls ? (
            <>
              <div className="download-publications-load-section">
                <span className="download-publications-section-label">
                  Publicaciones de Mercado Libre
                </span>

                <button
                  type="button"
                  className="download-publications-load-button"
                  onClick={handleLoadPublications}
                  disabled={isSyncing}
                >
                  {isSyncing ? (
                    <LoaderCircle
                      className="download-publications-button-spinner"
                      size={20}
                      aria-hidden="true"
                    />
                  ) : (
                    <CloudDownload size={20} aria-hidden="true" />
                  )}
                  {isSyncing
                    ? "Cargando Publicaciones..."
                    : "Cargar Publicaciones"}
                </button>

                {(syncState?.status && syncState.status !== "idle") ||
                syncError ? (
                  <div
                    className={`download-publications-sync-status ${
                      syncError || syncState?.status === "error"
                        ? "download-publications-sync-status--error"
                        : ""
                    }`}
                    aria-live="polite"
                  >
                    <div className="download-publications-sync-heading">
                      <span>
                        {syncError ||
                          syncState?.last_error ||
                          syncState?.message ||
                          "Preparando carga..."}
                      </span>
                      <strong>{Number(syncState?.progress || 0)}%</strong>
                    </div>

                    <div className="download-publications-progress-track">
                      <span
                        style={{
                          width: `${Math.min(
                            100,
                            Math.max(0, Number(syncState?.progress || 0))
                          )}%`,
                        }}
                      />
                    </div>

                    {syncState && (
                      <div className="download-publications-sync-metrics">
                        <span>
                          Listadas:{" "}
                          {Number(syncState.scanned_count || 0).toLocaleString(
                            "es-CL"
                          )}
                        </span>
                        <span>
                          Guardadas:{" "}
                          {Number(syncState.saved_count || 0).toLocaleString(
                            "es-CL"
                          )}
                        </span>
                        <span>
                          Sin detalle:{" "}
                          {Number(syncState.failed_count || 0).toLocaleString(
                            "es-CL"
                          )}
                        </span>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>

              <div
                className="download-publications-divider"
                aria-hidden="true"
              />
            </>
          ) : null}

          <div className="download-publications-filter-row">
            <div className="download-publications-date-field">
              <label htmlFor="publication-date">
                Fecha de Publicaciones
              </label>

              <div className="download-publications-date-control">
                <CalendarDays size={19} aria-hidden="true" />
                <input
                  id="publication-date"
                  type="date"
                  value={publicationDate}
                  disabled={isExportBusy}
                  onChange={(event) => {
                    setPublicationDate(event.target.value);
                    setExportJob(null);
                    setExportError("");
                    downloadedExportRef.current = "";
                    window.localStorage.removeItem(exportJobStorageKey);
                    window.localStorage.removeItem(downloadedExportStorageKey);
                  }}
                />
              </div>
            </div>

            <button
              type="button"
              className="download-publications-download-button"
              onClick={handleDownloadPublications}
              disabled={isExportBusy || !publicationDate}
            >
              {isExportBusy ? (
                <LoaderCircle
                  className="download-publications-button-spinner"
                  size={19}
                  aria-hidden="true"
                />
              ) : (
                <Download size={19} aria-hidden="true" />
              )}
              {isExportBusy
                ? "Generando Excel..."
                : exportJob?.download_ready
                  ? "Descargar Excel"
                  : "Descargar Publicaciones"}
            </button>
          </div>

          {exportJob || exportError ? (
            <div
              className={`download-publications-sync-status download-publications-export-status ${
                exportError || exportJob?.status === "error"
                  ? "download-publications-sync-status--error"
                  : ""
              }`}
              aria-live="polite"
            >
              <div className="download-publications-sync-heading">
                <span>
                  {exportError ||
                    (exportJob?.status === "error"
                      ? exportJob?.last_error
                      : "") ||
                    exportJob?.message ||
                    "Preparando exportacion..."}
                </span>
                <strong>{Number(exportJob?.progress || 0)}%</strong>
              </div>

              {exportJob ? (
                <>
                  <div className="download-publications-progress-track">
                    <span
                      style={{
                        width: `${Math.min(
                          100,
                          Math.max(0, Number(exportJob.progress || 0))
                        )}%`,
                      }}
                    />
                  </div>

                  <div className="download-publications-sync-metrics">
                    <span>
                      Procesadas:{" "}
                      {Number(exportJob.processed_rows || 0).toLocaleString(
                        "es-CL"
                      )}
                      /
                      {Number(exportJob.total_rows || 0).toLocaleString(
                        "es-CL"
                      )}
                    </span>
                    <span>
                      Con descripcion:{" "}
                      {Number(
                        exportJob.descriptions_found || 0
                      ).toLocaleString("es-CL")}
                    </span>
                    <span>
                      Sin descripcion:{" "}
                      {Number(
                        exportJob.descriptions_missing || 0
                      ).toLocaleString("es-CL")}
                    </span>
                    <span>
                      Con error:{" "}
                      {Number(
                        exportJob.descriptions_failed || 0
                      ).toLocaleString("es-CL")}
                    </span>
                    <span>
                      Desde cache:{" "}
                      {Number(exportJob.cache_hits || 0).toLocaleString(
                        "es-CL"
                      )}
                    </span>
                    <span>
                      Reintentos:{" "}
                      {Number(exportJob.retry_count || 0).toLocaleString(
                        "es-CL"
                      )}
                    </span>
                    <span>
                      Ritmo:{" "}
                      {Number(
                        exportJob.requests_per_second || 0
                      ).toLocaleString("es-CL")}{" "}
                      req/s
                    </span>
                    <span>
                      Concurrencia:{" "}
                      {Number(
                        exportJob.http_concurrency || 0
                      ).toLocaleString("es-CL")}
                    </span>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
