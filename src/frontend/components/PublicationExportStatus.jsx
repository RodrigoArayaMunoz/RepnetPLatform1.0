export default function PublicationExportStatus({ job, error }) {
  if (!job && !error) return null;
  return (
    <div
      className={`download-publications-sync-status download-publications-export-status ${
        error || job?.status === "error" ? "download-publications-sync-status--error" : ""
      }`}
      aria-live="polite"
    >
      <div className="download-publications-sync-heading">
        <span>
          {error || (job?.status === "error" ? job.last_error : "") || job?.message || "Preparando exportación..."}
        </span>
        <strong>{Number(job?.progress || 0)}%</strong>
      </div>
      {job && (
        <>
          <div className="download-publications-progress-track">
            <span style={{ width: `${Math.min(100, Math.max(0, Number(job.progress || 0)))}%` }} />
          </div>
          <div className="download-publications-sync-metrics">
            <span>
              Procesadas: {Number(job.processed_rows || 0).toLocaleString("es-CL")}
              /{Number(job.total_rows || 0).toLocaleString("es-CL")}
            </span>
            <span>Reintentos: {Number(job.retry_count || 0).toLocaleString("es-CL")}</span>
          </div>
        </>
      )}
    </div>
  );
}
