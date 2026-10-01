import { useEffect, useId, useRef } from "react";
import { Check, LoaderCircle } from "lucide-react";
import "./RefaxDownloadModal.css";

export default function RefaxDownloadModal({ progress, complete }) {
  const dialogRef = useRef(null);
  const titleId = useId();
  const descriptionId = useId();
  const percentage = complete ? 100 : progress.percentage;
  const message = complete
    ? "Tu Excel está listo. La descarga se ha iniciado."
    : progress.message || "Iniciando la preparación del Excel…";

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousOverflow = document.body.style.overflow;
    const preventClose = (event) => event.preventDefault();
    const preventEscape = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    dialog.addEventListener("cancel", preventClose);
    document.addEventListener("keydown", preventEscape, true);
    dialog.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog.close();
      dialog.removeEventListener("cancel", preventClose);
      document.removeEventListener("keydown", preventEscape, true);
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="refax-download-modal"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-modal="true"
      closedby="none"
    >
      <div className="refax-download-modal__content" aria-busy={!complete}>
        <span className="refax-download-modal__icon" aria-hidden="true">
          {complete ? (
            <Check size={34} strokeWidth={2.5} />
          ) : (
            <LoaderCircle className="refax-download-modal__spinner" size={38} />
          )}
        </span>
        <h2 id={titleId}>{complete ? "Excel listo" : "Preparando Excel"}</h2>
        <p id={descriptionId} role="status">{message}</p>
        <div className="refax-download-modal__progress-label">
          <span>Avance del proceso</span>
          <strong>{percentage === null ? "Calculando…" : `${percentage}%`}</strong>
        </div>
        <progress
          className="refax-download-modal__progress"
          max="100"
          value={percentage === null ? undefined : percentage}
          aria-label="Avance del proceso de exportación"
        />
        <small>
          {progress.stage === "receiving" && !complete
            ? "Recibiendo el catálogo; después se generará el Excel."
            : "Esta ventana se cerrará automáticamente al terminar."}
        </small>
      </div>
    </dialog>
  );
}
