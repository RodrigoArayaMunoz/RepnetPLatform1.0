import "./ProcessQueueErrorModal.css";

export default function ProcessQueueErrorModal({
  row,
  errorData,
  isLoading,
  loadError,
  onClose,
}) {
  if (!row) return null;

  const failedItems = Array.isArray(errorData?.failed_items)
    ? errorData.failed_items.filter(Boolean)
    : [];
  const messages = Array.isArray(errorData?.messages)
    ? errorData.messages.filter(Boolean)
    : [];
  const isPartialProcess = failedItems.length > 0;
  const successCount = Number(errorData?.success_count || 0);
  const primaryMessage =
    errorData?.message ||
    loadError ||
    "No se encontraron detalles adicionales para este error.";

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
                <p>{primaryMessage}</p>
                {isPartialProcess && successCount > 0 && (
                  <p className="pq-error-modal__summary-success">
                    {`Se han actualizado correctamente ${successCount} MLC.`}
                  </p>
                )}
              </section>

              {isPartialProcess ? (
                <section className="pq-error-modal__panel">
                  <span className="pq-error-modal__panel-label">
                    MLC con error
                  </span>
                  <ul className="pq-error-modal__mlc-list">
                    {failedItems.map((itemId) => (
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

              {!isPartialProcess && errorData?.traceback && (
                <details className="pq-error-modal__traceback">
                  <summary>Ver detalle técnico</summary>
                  <pre>{errorData.traceback}</pre>
                </details>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
