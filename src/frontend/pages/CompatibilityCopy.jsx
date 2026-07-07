import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  CopyPlus,
  FileSpreadsheet,
  Upload,
} from "lucide-react";
import * as XLSX from "xlsx";
import { authFetch } from "../../lib/apiClient.js";
import "../styles/CompatibilityCopy.css";

const ORIGIN_HEADER = "MLC-ORIGEN";
const DESTINATION_HEADER = "MLC-DESTINO";
const HISTORY_STORAGE_KEY = "compatibilityCopyHistory";
const HISTORY_PAGE_SIZE = 4;

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

    return Array.isArray(parsedHistory) ? parsedHistory : [];
  } catch {
    return [];
  }
};

const isExcelFile = (file) => {
  if (!file) return false;

  return /\.(xlsx|xls)$/i.test(file.name || "");
};

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

const copyItemCompatibilities = async (apiBase, originItemId, destinationItemId) => {
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

    throw new Error(
      detailMessage ||
        data?.error ||
        `No se pudo copiar compatibilidades hacia ${destinationItemId}`
    );
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
  const copiedCompatibilitiesCount = results.filter((result) => result.ok).length;
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

    try {
      setIsProcessing(true);
      setStatus("processing");
      setMessage("Validando archivo Excel...");
      setProcessedRows(0);
      setTotalRows(0);
      setResults([]);

      const rows = await parseCompatibilityRows(file);
      setTotalRows(rows.length);
      setMessage(`Procesando 0/${rows.length} filas...`);

      const nextResults = [];

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
            };
          }
        }

        nextResults.push(result);
        setResults([...nextResults]);
        setProcessedRows(nextResults.length);
        setMessage(`Procesando ${nextResults.length}/${rows.length} filas...`);
      }

      const successfulCopies = nextResults.filter((result) => result.ok).length;

      setProcessHistory((currentHistory) => [
        {
          id: `${Date.now()}-${file.name}`,
          fileName: file.name,
          copiedCount: successfulCopies,
          createdAt: new Date().toISOString(),
          status: "Completado",
        },
        ...currentHistory,
      ]);
      setHistoryPage(0);
      setStatus("success");
      setMessage("Proceso Finalizado");
    } catch (error) {
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

            {results.length > 0 && status === "success" && (
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
                  <th>Archivo</th>
                  <th>Compatibilidades</th>
                  <th>Fecha</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {visibleHistory.length > 0 ? (
                  visibleHistory.map((entry) => (
                    <tr key={entry.id}>
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
                      <td>
                        <span className="compat-copy-status-pill">
                          {entry.status || "Completado"}
                        </span>
                      </td>
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
    </section>
  );
}
