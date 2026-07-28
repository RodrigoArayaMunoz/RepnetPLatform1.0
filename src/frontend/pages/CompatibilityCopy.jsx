import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  CopyPlus,
  FileSpreadsheet,
  Upload,
  X,
} from "lucide-react";
import * as XLSX from "xlsx";
import { authFetch } from "../../lib/apiClient.js";
import "../styles/CompatibilityCopy.css";

const ORIGIN_HEADER = "MLC-ORIGEN";
const DESTINATION_HEADER = "MLC-DESTINO";
const HISTORY_STORAGE_KEY = "compatibilityCopyHistory";
const HISTORY_PAGE_SIZE = 4;
const PROCESS_OK = "PROCESO OK";
const PROCESS_WITH_ERRORS = "PROCESO CON ERRORES";
const PROCESSING = "PROCESANDO";

const formatHistoryDate = (value) => {
  if (!value) return "Ahora";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Ahora";

  return new Intl.DateTimeFormat("es-CL", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
};

const getInitialHistory = () => {
  if (typeof window === "undefined") return [];

  try {
    const storedHistory = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    const parsedHistory = storedHistory ? JSON.parse(storedHistory) : [];

    if (!Array.isArray(parsedHistory)) return [];

    return parsedHistory.map((entry) => {
      const sanitizedEntry = { ...entry };
      delete sanitizedEntry.status;
      return sanitizedEntry;
    });
  } catch {
    return [];
  }
};

const isExcelFile = (file) => {
  if (!file) return false;

  return /\.(xlsx|xls)$/i.test(file.name || "");
};

const getHistoryErrors = (entry) =>
  Array.isArray(entry?.errors) ? entry.errors : [];

const getHistoryErrorCount = (entry) => {
  const storedErrorCount = Number(entry?.errorCount);
  if (Number.isFinite(storedErrorCount) && storedErrorCount >= 0) {
    return storedErrorCount;
  }

  return getHistoryErrors(entry).length;
};

const getProcessResult = (entry) => {
  if (entry?.processResult === PROCESSING) return PROCESSING;
  if (entry?.processResult === PROCESS_WITH_ERRORS) {
    return PROCESS_WITH_ERRORS;
  }
  if (entry?.processResult === PROCESS_OK) return PROCESS_OK;

  return getHistoryErrorCount(entry) > 0
    ? PROCESS_WITH_ERRORS
    : PROCESS_OK;
};

const buildFailedRow = (result) => ({
  rowNumber: result.rowNumber,
  origin: result.origin || "",
  destination: result.destination || "",
  message: result.statusText || "No se pudo procesar la copia",
  statusCode: result.statusCode || null,
});

const normalizeHeader = (value) =>
  String(value ?? "")
    .trim()
    .toUpperCase();

const getCellText = (worksheet, rowIndex, columnIndex) => {
  const cellAddress = XLSX.utils.encode_cell({
    r: rowIndex,
    c: columnIndex,
  });

  return String(worksheet[cellAddress]?.v ?? "").trim();
};

const copyItemCompatibilities = async (
  apiBase,
  originItemId,
  destinationItemId
) => {
  const response = await authFetch(
    `${apiBase}/ml/items/${encodeURIComponent(
      destinationItemId
    )}/compatibilities/copy`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        origin_item_id: originItemId,
      }),
    }
  );
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = data?.detail;
    const detailMessage =
      typeof detail === "string" ? detail : detail?.message || data?.message;

    const requestError = new Error(
      detailMessage ||
        data?.error ||
        `No se pudo copiar compatibilidades hacia ${destinationItemId}`
    );
    requestError.statusCode = response.status;
    throw requestError;
  }

  return data;
};

export default function CompatibilityCopy() {
  const fileInputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("idle");
  const [isProcessing, setIsProcessing] = useState(false);
  const [processedRows, setProcessedRows] = useState(0);
  const [totalRows, setTotalRows] = useState(0);
  const [results, setResults] = useState([]);
  const [processHistory, setProcessHistory] = useState(getInitialHistory);
  const [historyPage, setHistoryPage] = useState(0);
  const [selectedErrorHistoryId, setSelectedErrorHistoryId] = useState(null);
  const copiedCompatibilitiesCount = results.filter((result) => result.ok).length;
  const selectedErrorHistory = processHistory.find(
    (entry) => entry.id === selectedErrorHistoryId
  );
  const historyPageCount = Math.max(
    1,
    Math.ceil(processHistory.length / HISTORY_PAGE_SIZE)
  );
  const visibleHistory = processHistory.slice(
    historyPage * HISTORY_PAGE_SIZE,
    (historyPage + 1) * HISTORY_PAGE_SIZE
  );

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  useEffect(() => {
    try {
      window.localStorage.setItem(
        HISTORY_STORAGE_KEY,
        JSON.stringify(processHistory)
      );
    } catch {
      // El historial visible se mantiene aunque localStorage no este disponible.
    }
  }, [processHistory]);

  useEffect(() => {
    if (!selectedErrorHistoryId) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setSelectedErrorHistoryId(null);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedErrorHistoryId]);

  const handleSelectFile = () => {
    fileInputRef.current?.click();
  };

  const setSelectedFile = (selectedFile) => {
    if (selectedFile && !isExcelFile(selectedFile)) {
      setFile(null);
      setMessage("Archivo no valido. Selecciona un Excel (.xlsx o .xls).");
      setStatus("error");
      setResults([]);
      return;
    }

    setFile(selectedFile);
    setMessage("");
    setStatus("idle");
    setProcessedRows(0);
    setTotalRows(0);
    setResults([]);
  };

  const handleFileChange = (event) => {
    const selectedFile = event.target.files?.[0] ?? null;

    setSelectedFile(selectedFile);
  };

  const handleDropFile = (event) => {
    event.preventDefault();
    const droppedFile = event.dataTransfer.files?.[0] ?? null;

    setSelectedFile(droppedFile);
  };

  const parseCompatibilityRows = async (selectedFile) => {
    const fileBuffer = await selectedFile.arrayBuffer();
    const workbook = XLSX.read(fileBuffer, {
      type: "array",
      cellDates: true,
    });
    const sheetName = workbook.SheetNames[0];
    const worksheet = workbook.Sheets[sheetName];

    if (!worksheet || !worksheet["!ref"]) {
      throw new Error("El archivo Excel no contiene hojas con datos.");
    }

    const range = XLSX.utils.decode_range(worksheet["!ref"]);
    const headerRowIndex = range.s.r;
    const originColumnIndex = range.s.c;
    const destinationColumnIndex = range.s.c + 1;
    const originHeader = normalizeHeader(
      getCellText(worksheet, headerRowIndex, originColumnIndex)
    );
    const destinationHeader = normalizeHeader(
      getCellText(worksheet, headerRowIndex, destinationColumnIndex)
    );

    if (
      originHeader !== ORIGIN_HEADER ||
      destinationHeader !== DESTINATION_HEADER
    ) {
      throw new Error(
        `Formato no valido. La primera fila debe tener ${ORIGIN_HEADER} y ${DESTINATION_HEADER}.`
      );
    }

    const rows = [];
    for (let rowIndex = headerRowIndex + 1; rowIndex <= range.e.r; rowIndex += 1) {
      const origin = getCellText(worksheet, rowIndex, originColumnIndex);
      const destination = getCellText(
        worksheet,
        rowIndex,
        destinationColumnIndex
      );

      if (!origin && !destination) {
        continue;
      }

      rows.push({
        rowNumber: rowIndex + 1,
        origin,
        destination,
      });
    }

    if (rows.length === 0) {
      throw new Error("El archivo no tiene filas para procesar.");
    }

    return rows;
  };

  const handleCopyCompatibilities = async () => {
    if (!file || isProcessing) return;

    const selectedFile = file;
    let activeProcessId = null;
    let rows = [];
    const nextResults = [];

    try {
      setIsProcessing(true);
      setStatus("processing");
      setMessage("Validando archivo Excel...");
      setProcessedRows(0);
      setTotalRows(0);
      setResults([]);

      rows = await parseCompatibilityRows(selectedFile);
      activeProcessId = `${Date.now()}-${selectedFile.name}`;
      const createdAt = new Date().toISOString();

      setTotalRows(rows.length);
      setMessage(`Procesando 0/${rows.length} filas...`);
      setProcessHistory((currentHistory) => [
        {
          id: activeProcessId,
          fileName: selectedFile.name,
          totalRows: rows.length,
          processedCount: 0,
          copiedCount: 0,
          errorCount: 0,
          errors: [],
          createdAt,
          processResult: PROCESSING,
        },
        ...currentHistory,
      ]);
      setHistoryPage(0);

      for (const row of rows) {
        let result;

        if (!row.origin) {
          result = {
            ...row,
            ok: false,
            hadCompatibilities: false,
            method: null,
            statusText: "MLC-ORIGEN vacio",
          };
        } else if (!row.destination) {
          result = {
            ...row,
            ok: false,
            hadCompatibilities: false,
            method: null,
            statusText: "MLC-DESTINO vacio",
          };
        } else {
          try {
            const copyResult = await copyItemCompatibilities(
              API_BASE,
              row.origin,
              row.destination
            );
            result = {
              ...row,
              ok: true,
              hadCompatibilities: Boolean(copyResult?.had_compatibilities),
              method: copyResult?.method || "",
              statusText:
                copyResult?.method === "PUT"
                  ? "Tenia compatibilidades: copia aplicada con PUT"
                  : "No tenia compatibilidades: copia aplicada con POST",
            };
          } catch (error) {
            result = {
              ...row,
              ok: false,
              hadCompatibilities: false,
              method: null,
              statusText:
                error?.message || "Error copiando compatibilidades",
              statusCode: error?.statusCode || null,
            };
          }
        }

        nextResults.push(result);
        const failedRows = nextResults
          .filter((currentResult) => !currentResult.ok)
          .map(buildFailedRow);
        const successfulCopies = nextResults.length - failedRows.length;
        const processFinished = nextResults.length === rows.length;

        setResults([...nextResults]);
        setProcessedRows(nextResults.length);
        setMessage(`Procesando ${nextResults.length}/${rows.length} filas...`);
        setProcessHistory((currentHistory) =>
          currentHistory.map((entry) =>
            entry.id === activeProcessId
              ? {
                  ...entry,
                  processedCount: nextResults.length,
                  copiedCount: successfulCopies,
                  errorCount: failedRows.length,
                  errors: failedRows,
                  processResult: processFinished
                    ? failedRows.length > 0
                      ? PROCESS_WITH_ERRORS
                      : PROCESS_OK
                    : PROCESSING,
                }
              : entry
          )
        );
      }

      const successfulCopies = nextResults.filter((result) => result.ok).length;
      const failedCopies = nextResults.length - successfulCopies;

      setStatus(failedCopies > 0 ? "warning" : "success");
      setMessage(
        failedCopies > 0
          ? `Proceso Finalizado con ${failedCopies} fila(s) con error`
          : "Proceso Finalizado"
      );
    } catch (error) {
      if (activeProcessId) {
        const unprocessedRows = rows
          .slice(nextResults.length)
          .map((row) => ({
            ...row,
            ok: false,
            statusText: "La copia no alcanzó a procesarse",
          }));
        const finalResults = [...nextResults, ...unprocessedRows];
        const failedRows = finalResults
          .filter((result) => !result.ok)
          .map(buildFailedRow);
        const successfulCopies = finalResults.length - failedRows.length;

        setResults(finalResults);
        setProcessHistory((currentHistory) =>
          currentHistory.map((entry) =>
            entry.id === activeProcessId
              ? {
                  ...entry,
                  processedCount: nextResults.length,
                  copiedCount: successfulCopies,
                  errorCount: failedRows.length,
                  errors: failedRows,
                  processResult: PROCESS_WITH_ERRORS,
                }
              : entry
          )
        );
      }

      setStatus("error");
      setMessage(error?.message || "No se pudo procesar el archivo.");
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <section className="compat-copy-page">
      <div className="compat-copy-layout">
        <header className="compat-copy-header">
          <h1 className="compat-copy-title">Copia de Compatibilidades</h1>
          <p className="compat-copy-subtitle">
            Duplica configuraciones de compatibilidad entre SKUs masivamente via Excel.
          </p>
        </header>

        <div className="compat-copy-card">
          <div className="compat-copy-card-grid">
            <div className="compat-copy-upload-panel">
              <span className="compat-copy-section-label">
                Archivo fuente (Excel)
              </span>

              <input
                ref={fileInputRef}
                className="compat-copy-file-input"
                type="file"
                accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
                onChange={handleFileChange}
              />

              <button
                className="compat-copy-dropzone"
                type="button"
                onClick={handleSelectFile}
                onDragOver={(event) => event.preventDefault()}
                onDrop={handleDropFile}
              >
                <Upload size={24} />
                <span>
                  Arrastra tu archivo o <strong>selecciona uno</strong>
                </span>
                <small>XLSX, XLS hasta 10MB</small>
              </button>

              <p className="compat-copy-file-name">
                {file ? file.name : "Ningun archivo seleccionado"}
              </p>
            </div>

            <div className="compat-copy-actions-panel">
              <button
                className="compat-copy-process-button"
                type="button"
                onClick={handleCopyCompatibilities}
                disabled={!file || isProcessing}
              >
                <CopyPlus size={18} />
                <span>
                  {isProcessing ? "Procesando..." : "Ejecutar copia masiva"}
                </span>
              </button>

              <span className="compat-copy-estimate">
                Tiempo estimado del proceso: ~45 segundos
              </span>
            </div>

            {message && (
              <p className={`compat-copy-message ${status}`}>{message}</p>
            )}

            {totalRows > 0 && (
              <div className="compat-copy-progress">
                <span>
                  {processedRows}/{totalRows}
                </span>
                <div className="compat-copy-progress-bar">
                  <div
                    className="compat-copy-progress-fill"
                    style={{
                      width: `${Math.round((processedRows / totalRows) * 100)}%`,
                    }}
                  />
                </div>
              </div>
            )}

            {results.length > 0 &&
              (status === "success" ||
                status === "warning" ||
                status === "error") && (
              <div className="compat-copy-summary" aria-live="polite">
                <p className="compat-copy-summary-label">
                  Nro. de compatibilidades copiadas:
                </p>
                <strong className="compat-copy-summary-value">
                  {copiedCompatibilitiesCount}
                </strong>
              </div>
            )}
          </div>
        </div>

        <section className="compat-copy-history" aria-label="Historial de procesos">
          <div className="compat-copy-history-heading">
            <h2 className="compat-copy-history-title">Historial de procesos</h2>
            {processHistory.length > HISTORY_PAGE_SIZE && (
              <span className="compat-copy-history-see-all">Ver todo</span>
            )}
          </div>

          <div className="compat-copy-history-table-wrap">
            <table className="compat-copy-history-table">
              <thead>
                <tr>
                  <th>Resultado Proceso</th>
                  <th>Archivo</th>
                  <th>Compatibilidades Copiadas</th>
                  <th>Fecha</th>
                </tr>
              </thead>
              <tbody>
                {visibleHistory.length > 0 ? (
                  visibleHistory.map((entry) => (
                    <tr key={entry.id}>
                      <td>
                        {getProcessResult(entry) === PROCESS_WITH_ERRORS ? (
                          <button
                            className="compat-copy-result-pill has-errors"
                            type="button"
                            onClick={() =>
                              setSelectedErrorHistoryId(entry.id)
                            }
                            aria-label={`Ver ${getHistoryErrorCount(
                              entry
                            )} errores de ${entry.fileName}`}
                          >
                            <AlertTriangle size={14} />
                            <span>{PROCESS_WITH_ERRORS}</span>
                            <strong className="compat-copy-result-error-count">
                              {getHistoryErrorCount(entry)}
                            </strong>
                          </button>
                        ) : (
                          <span
                            className={`compat-copy-result-pill ${
                              getProcessResult(entry) === PROCESSING
                                ? "processing"
                                : "is-ok"
                            }`}
                          >
                            {getProcessResult(entry)}
                          </span>
                        )}
                      </td>
                      <td title={entry.fileName}>
                        <span className="compat-copy-file-cell-icon">
                          <FileSpreadsheet size={17} />
                        </span>
                        <span className="compat-copy-file-cell-name">
                          {entry.fileName}
                        </span>
                      </td>
                      <td>{entry.copiedCount}</td>
                      <td>{formatHistoryDate(entry.createdAt)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="4" className="compat-copy-history-empty">
                      Sin procesos registrados
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {processHistory.length > HISTORY_PAGE_SIZE && (
            <div className="compat-copy-history-pagination">
              <button
                className="compat-copy-history-page-button"
                type="button"
                onClick={() =>
                  setHistoryPage((currentPage) =>
                    Math.max(currentPage - 1, 0)
                  )
                }
                disabled={historyPage === 0}
                aria-label="Pagina anterior"
              >
                <ChevronLeft size={16} />
              </button>

              <span className="compat-copy-history-page-status">
                {historyPage + 1}/{historyPageCount}
              </span>

              <button
                className="compat-copy-history-page-button"
                type="button"
                onClick={() =>
                  setHistoryPage((currentPage) =>
                    Math.min(currentPage + 1, historyPageCount - 1)
                  )
                }
                disabled={historyPage >= historyPageCount - 1}
                aria-label="Pagina siguiente"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}
        </section>
      </div>

      {selectedErrorHistory && (
        <div
          className="compat-copy-error-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedErrorHistoryId(null);
            }
          }}
        >
          <section
            className="compat-copy-error-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="compat-copy-error-modal-title"
          >
            <header className="compat-copy-error-modal-header">
              <span className="compat-copy-error-modal-icon">
                <AlertTriangle size={22} />
              </span>
              <div>
                <h2 id="compat-copy-error-modal-title">
                  Errores del proceso de copia
                </h2>
                <p title={selectedErrorHistory.fileName}>
                  {selectedErrorHistory.fileName}
                </p>
              </div>
              <button
                className="compat-copy-error-modal-close"
                type="button"
                onClick={() => setSelectedErrorHistoryId(null)}
                aria-label="Cerrar detalle de errores"
                autoFocus
              >
                <X size={20} />
              </button>
            </header>

            <div className="compat-copy-error-modal-summary">
              <strong>
                {getHistoryErrorCount(selectedErrorHistory)}
              </strong>
              <span>
                fila(s) no pudieron copiar sus compatibilidades.
              </span>
            </div>

            <div className="compat-copy-error-table-wrap">
              <table className="compat-copy-error-table">
                <thead>
                  <tr>
                    <th>Fila Excel</th>
                    <th>MLC-ORIGEN</th>
                    <th>MLC-DESTINO</th>
                    <th>Detalle del error</th>
                  </tr>
                </thead>
                <tbody>
                  {getHistoryErrors(selectedErrorHistory).length > 0 ? (
                    getHistoryErrors(selectedErrorHistory).map(
                      (errorRow, index) => (
                        <tr
                          key={`${errorRow.rowNumber}-${errorRow.origin}-${errorRow.destination}-${index}`}
                        >
                          <td>{errorRow.rowNumber || "—"}</td>
                          <td>{errorRow.origin || "—"}</td>
                          <td>{errorRow.destination || "—"}</td>
                          <td>
                            {errorRow.statusCode
                              ? `HTTP ${errorRow.statusCode}: `
                              : ""}
                            {errorRow.message}
                          </td>
                        </tr>
                      )
                    )
                  ) : (
                    <tr>
                      <td colSpan="4" className="compat-copy-error-table-empty">
                        El detalle por MLC no está disponible para este registro
                        histórico.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <footer className="compat-copy-error-modal-footer">
              <button
                type="button"
                onClick={() => setSelectedErrorHistoryId(null)}
              >
                Cerrar
              </button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}
