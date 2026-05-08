import "./ProcessQueueErrorModal.css";

export default function ProcessQueueErrorModal({
  row,
  errorData,
  isLoading,
  loadError,
  onClose,
}) {
  if (!row) return null;

  const messages = Array.isArray(errorData?.messages)
    ? errorData.messages.filter(Boolean)
    : [];
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
            <p className="pq-error-modal__eyebrow">Proceso con error</p>
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
              <section className="pq-error-modal__panel pq-error-modal__panel--danger">
                <span className="pq-error-modal__panel-label">
                  Mensaje principal
                </span>
                <p>{primaryMessage}</p>
              </section>

              {messages.length > 1 && (
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
              )}

              {errorData?.traceback && (
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
