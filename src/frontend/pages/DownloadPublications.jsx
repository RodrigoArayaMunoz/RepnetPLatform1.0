import { useCallback, useEffect, useState } from "react";
import {
  CalendarDays,
  CloudDownload,
  Download,
  LoaderCircle,
  Layers3,
} from "lucide-react";
import { authFetch } from "../../lib/apiClient.js";
import usePublicationExport, { readPublicationError } from "../hooks/usePublicationExport.js";
import PublicationExportStatus from "../components/PublicationExportStatus.jsx";
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

export default function DownloadPublications({ authUserId }) {
  const [publicationDate, setPublicationDate] = useState(getLocalToday);
  const [syncState, setSyncState] = useState(null);
  const [syncError, setSyncError] = useState("");
  const dateExport = usePublicationExport({ authUserId, onRestoreDate: setPublicationDate });
  const catalogExport = usePublicationExport({ authUserId, scope: "catalog" });
  const isSyncing = Boolean(syncState?.running);
  const isExportBusy = dateExport.isBusy || catalogExport.isBusy;

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
          readPublicationError(
            data,
            "No se pudo consultar el estado de las publicaciones."
          )
        );
      }

      setSyncState(data);
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
    if (isSyncing || isExportBusy) {
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
          readPublicationError(
            data,
            "No se pudo iniciar la carga de publicaciones."
          )
        );
      }

      setSyncState(data);
      dateExport.reset();
      catalogExport.reset();
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
            Descarga un Excel con MLC, SKU, NUMERO_PIEZA, TÍTULO, ESTADO y
            ¿POSEE COMPATIBILIDADES? Elige las publicaciones de una fecha o el
            catálogo completo guardado en la base de datos.
          </p>
        </header>

        <div className="download-publications-card">
          <div className="download-publications-load-section" hidden>
            <span className="download-publications-section-label">
              Publicaciones de Mercado Libre
            </span>

            <button
              type="button"
              className="download-publications-load-button"
              onClick={handleLoadPublications}
              disabled={isSyncing || isExportBusy}
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
            hidden
          />

          <h2 className="download-publications-card-title">Publicaciones por fecha</h2>
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
                    dateExport.reset();
                  }}
                />
              </div>
            </div>

            <button
              type="button"
              className="download-publications-download-button"
              onClick={() => { if (!isSyncing && !isExportBusy) dateExport.start(publicationDate); }}
              disabled={isExportBusy || isSyncing || !publicationDate}
            >
              {dateExport.isBusy ? (
                <LoaderCircle
                  className="download-publications-button-spinner"
                  size={19}
                  aria-hidden="true"
                />
              ) : (
                <Download size={19} aria-hidden="true" />
              )}
              {dateExport.isBusy
                ? "Generando Excel..."
                : dateExport.job?.download_ready
                  ? "Descargar Excel"
                  : "Descargar Publicaciones"}
            </button>
          </div>

          <PublicationExportStatus job={dateExport.job} error={dateExport.error} />
        </div>

        <section className="download-publications-card download-publications-catalog-card" aria-labelledby="catalog-title">
          <div className="download-publications-catalog-heading">
            <span className="download-publications-catalog-icon" aria-hidden="true">
              <Layers3 size={24} />
            </span>
            <div>
              <span className="download-publications-catalog-label">Mercado Libre · Todas las fechas</span>
              <h2 id="catalog-title" className="download-publications-card-title">Catálogo completo Emilia</h2>
            </div>
          </div>
          <p id="catalog-description" className="download-publications-catalog-description">
            Exporta todas las publicaciones guardadas, de cualquier fecha y estado,
            con las mismas columnas del Excel por fecha.
          </p>
          <button
            type="button"
            className="download-publications-catalog-button"
            aria-describedby="catalog-description"
            onClick={() => { if (!isSyncing && !isExportBusy) catalogExport.start(); }}
            disabled={isSyncing || isExportBusy}
          >
            {catalogExport.isBusy ? (
              <LoaderCircle className="download-publications-button-spinner" size={20} aria-hidden="true" />
            ) : (
              <Download size={20} aria-hidden="true" />
            )}
            {catalogExport.isBusy ? "Generando catálogo completo..." : "Descargar Catalogo Completo Emilia"}
          </button>
          <PublicationExportStatus job={catalogExport.job} error={catalogExport.error} />
        </section>
      </div>
    </section>
  );
}
