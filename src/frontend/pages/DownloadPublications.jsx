import { useCallback, useEffect, useState } from "react";
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

export default function DownloadPublications() {
  const [publicationDate, setPublicationDate] = useState(getLocalToday);
  const [syncState, setSyncState] = useState(null);
  const [syncError, setSyncError] = useState("");
  const [isSyncStatusLoaded, setIsSyncStatusLoaded] = useState(false);
  const isSyncing = Boolean(syncState?.running);
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
                  onChange={(event) => setPublicationDate(event.target.value)}
                />
              </div>
            </div>

            <button
              type="button"
              className="download-publications-download-button"
            >
              <Download size={19} aria-hidden="true" />
              Descargar Publicaciones
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
