import { useEffect, useState } from "react";
import { FileSpreadsheet } from "lucide-react";
import "./ProcessQueueErrorModal.css";
import { authFetch } from "../../lib/apiClient.js";

export default function ProcessQueueErrorModal({
  row,
  apiBase,
  errorData,
  isLoading,
  loadError,
  onClose,
}) {
  const [isDownloadingExcel, setIsDownloadingExcel] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  useEffect(() => {
    setIsDownloadingExcel(false);
    setDownloadError("");
  }, [row?.id, errorData?.occurred_at]);

  if (!row) return null;

  const failedItems = Array.isArray(errorData?.failed_items)
    ? errorData.failed_items.filter(Boolean)
    : [];
  const failedSkus = Array.isArray(errorData?.failed_skus)
    ? errorData.failed_skus.filter(Boolean)
    : [];
  const messages = Array.isArray(errorData?.messages)
    ? errorData.messages.filter(Boolean)
    : [];
  const isPartialProcess =
    Boolean(errorData?.is_partial) ||
    failedItems.length > 0 ||
    failedSkus.length > 0;
  const successCount = Number(errorData?.success_count || 0);
  const primaryMessage =
    errorData?.message ||
    loadError ||
    "No se encontraron detalles adicionales para este error.";
  const detailItems = failedItems.length > 0 ? failedItems : failedSkus;
  const detailLabel = failedItems.length > 0 ? "MLC con error" : "SKU con error";

  const handleDownloadExcel = async () => {
    if (!row?.id || !apiBase || failedItems.length === 0 || isDownloadingExcel) {
      return;
    }

    try {
      setIsDownloadingExcel(true);
      setDownloadError("");

      const response = await authFetch(
        `${apiBase}/process-queue/errors/${row.id}/export`,
        {
          method: "GET",
          credentials: "include",
        }
      );

      if (!response.ok) {
        const responseError = await response.json().catch(() => ({}));
        throw new Error(
          responseError?.detail ||
            responseError?.message ||
            "No se pudo descargar el archivo Excel."
        );
      }

      const blob = await response.blob();
      const contentDisposition =
        response.headers.get("Content-Disposition") || "";
      const filenameMatch = contentDisposition.match(/filename=\"?([^"]+)\"?/i);
      const filename =
        filenameMatch?.[1] || `mlc_con_error_${String(row.id)}.xlsx`;
      const downloadUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = downloadUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      setDownloadError(
        error?.message || "No se pudo descargar el archivo Excel."
      );
    } finally {
      setIsDownloadingExcel(false);
    }
  };

  return (
    <div
      className="pq-error-modal__overlay"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="pq-error-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pq-error-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="pq-error-modal__header">
          <div>
            <p
              className={`pq-error-modal__eyebrow ${
                isPartialProcess ? "pq-error-modal__eyebrow--warning" : ""
              }`}
            >
              {isPartialProcess ? "Proceso con errores" : "Proceso con error"}
            </p>
            <h2 id="pq-error-modal-title">Detalle del procesamiento</h2>
          </div>

          <button
            type="button"
            className="pq-error-modal__close"
            onClick={onClose}
            aria-label="Cerrar detalle de error"
          >
            ×
          </button>
        </div>

        <div className="pq-error-modal__body">
          {isLoading ? (
            <div className="pq-error-modal__state">
              Cargando detalle del error...
            </div>
          ) : (
            <>
              <section
                className={`pq-error-modal__panel ${
                  isPartialProcess
                    ? "pq-error-modal__panel--warning"
                    : "pq-error-modal__panel--danger"
                }`}
              >
                <span className="pq-error-modal__panel-label">
                  {isPartialProcess ? "Resumen" : "Mensaje principal"}
                </span>
                {isPartialProcess ? (
                  <div className="pq-error-modal__summary-row">
                    <p>{primaryMessage}</p>
                    <button
                      type="button"
                      className="pq-error-modal__excel-button"
                      onClick={handleDownloadExcel}
                      disabled={isDownloadingExcel}
                      aria-label="Descargar Excel con MLC con error"
                      title="Descargar Excel con MLC con error"
                    >
                      <FileSpreadsheet size={16} aria-hidden="true" />
                      <span>
                        {isDownloadingExcel
                          ? "Descargando..."
                          : "Descargar Excel de Errores"}
                      </span>
                    </button>
                  </div>
                ) : (
                  <p>{primaryMessage}</p>
                )}
                {isPartialProcess && successCount > 0 && (
                  <p className="pq-error-modal__summary-success">
                    {`Se han actualizado correctamente ${successCount} MLC.`}
                  </p>
                )}
                {downloadError && (
                  <p className="pq-error-modal__download-error">
                    {downloadError}
                  </p>
                )}
                {!isPartialProcess && errorData?.traceback && (
                  <p className="pq-error-modal__support-note">
                    Se registró un detalle técnico interno para revisión del
                    servidor.
                  </p>
                )}
              </section>

              {isPartialProcess && detailItems.length > 0 ? (
                <section className="pq-error-modal__panel">
                  <span className="pq-error-modal__panel-label">{detailLabel}</span>
                  <ul className="pq-error-modal__mlc-list">
                    {detailItems.map((itemId) => (
                      <li key={itemId} className="pq-error-modal__mlc-pill">
                        {itemId}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : messages.length > 1 ? (
                <section className="pq-error-modal__panel">
                  <span className="pq-error-modal__panel-label">
                    Mensajes detectados
                  </span>
                  <ul className="pq-error-modal__message-list">
                    {messages.map((message, index) => (
                      <li key={`${message}-${index}`}>{message}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
