import { useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CirclePlay,
  Clock3,
  Download,
  FileSpreadsheet,
  LoaderCircle,
  UserRound,
} from "lucide-react";
import {
  getProcessStatusTone,
  isCurrentProcess,
  PROCESS_STATUS,
} from "../utils/processBoard.js";

const ROWS_PER_PAGE = 3;
const LANES = [
  {
    key: "processing",
    title: "En ejecución",
    description: "Procesos que se están ejecutando",
    icon: CirclePlay,
    emptyTitle: "Sin procesos en ejecución",
    emptyDescription: "Ejecuta la cola de pendientes para comenzar.",
  },
  {
    key: "pending",
    title: "Pendientes",
    description: "En cola, listas para ejecutar",
    icon: Clock3,
    emptyTitle: "La cola está al día",
    emptyDescription: "Selecciona un archivo y guarda un nuevo proceso.",
  },
  {
    key: "completed",
    title: "Finalizados",
    description: "Resultados e historial de ejecución",
    icon: CheckCheck,
    emptyTitle: "Sin procesos finalizados",
    emptyDescription: "Los resultados aparecerán aquí al terminar.",
  },
];

export default function ProcessBoard({
  groups,
  isLoading,
  isQueueRunning,
  currentProcessRowId,
  currentProcessType,
  jobMessage,
  jobProcessedRows,
  jobTotalRows,
  jobCompatibilitiesCreated,
  exportingRowId,
  onOpenError,
  onDownloadResult,
}) {
  const [pages, setPages] = useState({});
  const [selectedLane, setSelectedLane] = useState(null);
  const visibleLane = selectedLane || (
    groups.processing.length > 0 ? "processing" :
    groups.pending.length > 0 ? "pending" : "completed"
  );
  const isInitialLoading =
    isLoading && Object.values(groups).every((rows) => rows.length === 0);

  return (
    <div className="process-board" aria-label="Procesos por estado" aria-busy={isLoading}>
      <nav className="process-board__tabs" aria-label="Estados de procesos">
        {LANES.map((lane) => (
          <button
            key={lane.key}
            type="button"
            className={`process-board__tab ${visibleLane === lane.key ? "process-board__tab--active" : ""}`}
            aria-pressed={visibleLane === lane.key}
            aria-controls={`process-board-${lane.key}`}
            onClick={() => setSelectedLane(lane.key)}
          >
            {lane.title}<span>{groups[lane.key].length}</span>
          </button>
        ))}
      </nav>
      {LANES.map((lane) => {
        const rows = groups[lane.key];
        const totalPages = Math.max(1, Math.ceil(rows.length / ROWS_PER_PAGE));
        const firstRowId = rows[0]?.id;
        const savedPage = pages[lane.key];
        const page = savedPage?.firstRowId === firstRowId
          ? Math.min(savedPage?.page || 1, totalPages)
          : 1;
        const startIndex = (page - 1) * ROWS_PER_PAGE;
        const visibleRows = rows.slice(startIndex, startIndex + ROWS_PER_PAGE);
        const LaneIcon = lane.icon;
        const isWaitingForNext = lane.key === "processing" && isQueueRunning;

        return (
          <section
            key={lane.key}
            id={`process-board-${lane.key}`}
            className={`process-board__lane process-board__lane--${lane.key} ${visibleLane === lane.key ? "process-board__lane--selected" : ""}`}
            aria-labelledby={`process-board-${lane.key}-title`}
          >
            <header className="process-board__header">
              <div className="process-board__heading">
                <span className="process-board__lane-icon">
                  <LaneIcon size={21} aria-hidden="true" />
                </span>
                <h2 id={`process-board-${lane.key}-title`}>{lane.title}</h2>
                <span className="process-board__count" aria-label={`${rows.length} procesos`}>
                  {rows.length}
                </span>
              </div>
              <p>{lane.description}</p>
            </header>

            <div className="process-board__content">
              {isInitialLoading ? (
                <div className="process-board__empty" role="status">
                  <LoaderCircle className="process-board__spinner" size={28} aria-hidden="true" />
                  <p>Cargando procesos...</p>
                </div>
              ) : rows.length === 0 ? (
                <div className="process-board__empty">
                  <span className="process-board__empty-icon">
                    {isWaitingForNext ? (
                      <LoaderCircle className="process-board__spinner" size={28} aria-hidden="true" />
                    ) : (
                      <LaneIcon size={28} aria-hidden="true" />
                    )}
                  </span>
                  <h3>{isWaitingForNext ? "Preparando el siguiente proceso" : lane.emptyTitle}</h3>
                  <p>{isWaitingForNext ? "La cola sigue en ejecución." : lane.emptyDescription}</p>
                </div>
              ) : (
                <ul
                  className="process-board__list"
                  style={{
                    gridTemplateRows: Array.from({ length: ROWS_PER_PAGE }, (_, index) =>
                      visibleRows[index] && isCurrentProcess(visibleRows[index], isQueueRunning, currentProcessRowId)
                        ? "minmax(0, 1.6fr)"
                        : "minmax(0, 1fr)"
                    ).join(" "),
                  }}
                >
                  {visibleRows.map((row, index) => {
                    const isActive = isCurrentProcess(row, isQueueRunning, currentProcessRowId);
                    const displayStatus = lane.key === "processing"
                      ? PROCESS_STATUS.PROCESSING
                      : lane.key === "pending"
                      ? PROCESS_STATUS.PENDING
                      : row.displayEstado || row.estado || "Sin estado";
                    const statusTone = getProcessStatusTone(displayStatus);
                    const showProgress = isActive && jobTotalRows > 0;
                    const progressPercent = showProgress
                      ? Math.max(0, Math.min(100, Math.round((jobProcessedRows / jobTotalRows) * 100)))
                      : 0;
                    const isExporting = exportingRowId === row.id;

                    return (
                      <li key={row.id} className={`process-board__item ${isActive ? "process-board__item--active" : ""}`}>
                        <div className="process-board__item-top">
                          {lane.key === "pending" ? (
                            <span className="process-board__queue-position">#{startIndex + index + 1} en cola</span>
                          ) : (
                            <FileSpreadsheet size={19} className="process-board__file-icon" aria-hidden="true" />
                          )}
                          <div className="process-board__item-controls">
                            <span className={`status-badge status-badge--${statusTone}`}>
                              {lane.key === "processing" && <span className="process-board__live-dot" aria-hidden="true" />}
                              {displayStatus}
                            </span>
                            {(row.hasErrorDetails || row.hasExportResult) && (
                              <div className="process-board__actions">
                              {row.hasErrorDetails && (
                                <button
                                  type="button"
                                  className={`process-board__action process-board__action--${row.isPartialProcess ? "partial" : "error"}`}
                                  onClick={() => onOpenError(row)}
                                  aria-label={`Ver detalle del proceso ${row.archivo}`}
                                  title="Ver detalle del proceso"
                                >
                                  <AlertTriangle size={15} aria-hidden="true" />
                                </button>
                              )}
                              {row.hasExportResult && (
                                <button
                                  type="button"
                                  className="process-board__action process-board__action--download"
                                  onClick={() => onDownloadResult(row)}
                                  aria-label={`${row.exportLabel || "Descargar resultado"}: ${row.archivo}`}
                                  title={row.exportLabel || "Descargar resultado"}
                                  disabled={isExporting}
                                >
                                  {isExporting ? <LoaderCircle size={15} className="process-board__spinner" aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
                                </button>
                              )}
                              </div>
                            )}
                          </div>
                        </div>
                        <h3 className="process-board__file-name" title={row.archivo}>{row.archivo || "Archivo sin nombre"}</h3>
                        <div className="process-board__metadata">
                          <span title={row.fecha}><CalendarDays size={14} aria-hidden="true" /><span>{row.fecha}</span></span>
                          <span title={row.procesadoPor}><UserRound size={14} aria-hidden="true" /><span>{row.procesadoPor || "Usuario no informado"}</span></span>
                        </div>

                        {lane.key === "processing" && isActive && (
                          <div className="process-board__progress" title={jobMessage || undefined}>
                            {showProgress ? (
                              <div className="process-board__progress-heading">
                                <div
                                  className="process-board__progress-bar"
                                  role="progressbar"
                                  aria-label={`Avance de ${row.archivo}`}
                                  aria-valuemin={0}
                                  aria-valuemax={100}
                                  aria-valuenow={progressPercent}
                                >
                                  <div className="process-board__progress-fill" style={{ width: `${progressPercent}%` }} />
                                </div>
                                <strong>{progressPercent}%</strong>
                              </div>
                            ) : (
                              <p className="process-board__working">
                                <LoaderCircle size={15} className="process-board__spinner" aria-hidden="true" />
                                Preparando archivo...
                              </p>
                            )}
                            <div className="process-board__progress-summary">
                              {showProgress && <p className="process-board__progress-detail">{jobProcessedRows} / {jobTotalRows} filas</p>}
                              {currentProcessType === "compatibilities" && (
                                <p className="process-board__created" title={`${jobCompatibilitiesCreated} compatibilidades agregadas`}><CircleCheck size={14} aria-hidden="true" />{jobCompatibilitiesCreated} compatibilidades</p>
                              )}
                            </div>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <footer className="process-board__footer">
              <span>{rows.length > 0 ? startIndex + 1 : 0}–{Math.min(startIndex + ROWS_PER_PAGE, rows.length)} de {rows.length}</span>
              <nav className="process-board__pagination" aria-label={`Paginación de ${lane.title.toLowerCase()}`}>
                <button
                  type="button"
                  aria-label={`Página anterior de ${lane.title.toLowerCase()}`}
                  disabled={page === 1}
                  onClick={() => setPages((previous) => ({ ...previous, [lane.key]: { page: page - 1, firstRowId } }))}
                ><ChevronLeft size={16} aria-hidden="true" /></button>
                <span>{page} / {totalPages}</span>
                <button
                  type="button"
                  aria-label={`Página siguiente de ${lane.title.toLowerCase()}`}
                  disabled={page === totalPages}
                  onClick={() => setPages((previous) => ({ ...previous, [lane.key]: { page: page + 1, firstRowId } }))}
                ><ChevronRight size={16} aria-hidden="true" /></button>
              </nav>
            </footer>
          </section>
        );
      })}
    </div>
  );
}
