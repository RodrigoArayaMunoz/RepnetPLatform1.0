import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import ProcessQueueErrorModal from "../components/ProcessQueueErrorModal.jsx";
import "../styles/MainSyncJobs.css";
import { supabase } from "../../lib/supabase.js";

const SYNC_ROUTE = "/procesos/sincronizacion-procesos";
const PROCESS_BUCKET = "excel-procesos";
const PROCESS_STATUS = {
  PENDING: "Pendiente",
  PROCESSING: "Procesando",
  PROCESSED: "Procesado",
  PROCESSED_WITH_ERRORS: "Procesado con Errores",
  ERROR: "Error",
};

export default function MainSyncJobs() {
  const fileInputRef = useRef(null);

  const [selectedFile, setSelectedFile] = useState(null);
  const [now, setNow] = useState(new Date());
  const [processRows, setProcessRows] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoadingTable, setIsLoadingTable] = useState(true);
  const [statusMessage, setStatusMessage] = useState("");
  const [statusType, setStatusType] = useState("info");
  const [isQueueRunning, setIsQueueRunning] = useState(false);
  const [queueButtonText, setQueueButtonText] = useState("Ejecutar procesos");
  const [queueMessage, setQueueMessage] = useState("");
  const [queueCurrentProcessRowId, setQueueCurrentProcessRowId] = useState(null);
  const [jobProcessedRows, setJobProcessedRows] = useState(0);
  const [jobTotalRows, setJobTotalRows] = useState(0);
  const [selectedErrorRow, setSelectedErrorRow] = useState(null);
  const [selectedErrorDetails, setSelectedErrorDetails] = useState(null);
  const [isLoadingErrorDetails, setIsLoadingErrorDetails] = useState(false);
  const [errorDetailsLoadMessage, setErrorDetailsLoadMessage] = useState("");

  const [isConnectingMl, setIsConnectingMl] = useState(false);
  const [isCheckingMl, setIsCheckingMl] = useState(true);
  const [isMercadoLibreConnected, setIsMercadoLibreConnected] = useState(false);
  const [mlUserId, setMlUserId] = useState(null);

  const location = useLocation();
  const navigate = useNavigate();

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  useEffect(() => {
    const interval = setInterval(() => {
      setNow(new Date());
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    loadProcesses();
    checkMercadoLibreConnection();
    loadQueueStatus();
  }, []);

  useEffect(() => {
    const pollMs = isQueueRunning ? 5000 : 30000;
    const interval = setInterval(async () => {
      await loadQueueStatus();
      await loadProcesses();
    }, pollMs);

    return () => clearInterval(interval);
  }, [isQueueRunning]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const meliConnected = params.get("meli");
    const legacyConnected = params.get("ml_connected");

    if (meliConnected === "connected" || legacyConnected === "1") {
      checkMercadoLibreConnection();

      params.delete("meli");
      params.delete("ml_connected");
      params.delete("user_id");

      navigate(
        {
          pathname: SYNC_ROUTE,
          search: params.toString() ? `?${params.toString()}` : "",
        },
        { replace: true }
      );
    }
  }, [location.search, navigate]);

  const formattedDate = useMemo(() => {
    return now.toLocaleDateString("es-CL", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  }, [now]);

  const formattedTime = useMemo(() => {
    return now.toLocaleTimeString("es-CL", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }, [now]);

  const sqlDate = useMemo(() => {
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }, [now]);

  const sqlTime = useMemo(() => {
    const hours = String(now.getHours()).padStart(2, "0");
    const minutes = String(now.getMinutes()).padStart(2, "0");
    const seconds = String(now.getSeconds()).padStart(2, "0");
    return `${hours}:${minutes}:${seconds}`;
  }, [now]);

  const pendingProcessCount = useMemo(() => {
    return processRows.filter(
      (row) =>
        String(row.estado || "").toLowerCase() ===
        PROCESS_STATUS.PENDING.toLowerCase()
    ).length;
  }, [processRows]);

  const hasPendingProcesses = pendingProcessCount > 0;

  const visibleQueueMessage = useMemo(() => {
    if (isQueueRunning) {
      return queueMessage;
    }

    if (hasPendingProcesses) {
      return pendingProcessCount === 1
        ? "Hay 1 proceso pendiente listo para ejecutar."
        : `Hay ${pendingProcessCount} procesos pendientes listos para ejecutar.`;
    }

    return queueMessage;
  }, [hasPendingProcesses, isQueueRunning, pendingProcessCount, queueMessage]);

  const handleFileChange = (event) => {
    const file = event.target.files?.[0] || null;
    setSelectedFile(file);
    setStatusMessage("");
    setStatusType("info");
  };

  const formatTableDateTime = (fechaProceso, horaProceso) => {
    if (!fechaProceso) return "-";

    const [year, month, day] = fechaProceso.split("-");
    const safeTime = horaProceso ? horaProceso.slice(0, 8) : "00:00:00";

    return `${day}-${month}-${year} ${safeTime}`;
  };

  const mapProcessRow = (row) => ({
    id: row.id,
    procesoId: row.proceso_id,
    archivo: row.archivo,
    fecha: formatTableDateTime(row.fecha_proceso, row.hora_proceso),
    procesadoPor: row.generado,
    estado: row.estado,
    displayEstado: row.estado,
    hasErrorDetails:
      String(row.estado || "").toLowerCase() ===
      PROCESS_STATUS.ERROR.toLowerCase(),
    isPartialProcess: false,
  });

  const loadProcessErrorSummaries = async (rows) => {
    const rowIds = rows
      .map((row) => String(row?.id || "").trim())
      .filter(Boolean);

    if (rowIds.length === 0) {
      return {};
    }

    const res = await fetch(`${API_BASE}/process-queue/errors/summaries`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({
        row_ids: rowIds,
      }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        data?.detail ||
          data?.message ||
          "No se pudieron obtener los resúmenes de error."
      );
    }

    return data?.items && typeof data.items === "object" ? data.items : {};
  };

  const loadProcesses = async () => {
    if (!supabase) {
      setProcessRows([]);
      setIsLoadingTable(false);
      setStatusMessage("Supabase no está configurado.");
      setStatusType("error");
      return;
    }

    try {
      setIsLoadingTable(true);

      const { data, error } = await supabase
        .from("procesos")
        .select("id, proceso_id, archivo, fecha_proceso, hora_proceso, generado, estado")
        .order("fecha_proceso", { ascending: false })
        .order("hora_proceso", { ascending: false });

      if (error) {
        console.error("Error al cargar procesos:", error);
        setStatusMessage("No se pudo cargar la tabla de procesos.");
        setStatusType("error");
        return;
      }

      const mappedRows = (data || []).map(mapProcessRow);

      try {
        const errorSummaries = await loadProcessErrorSummaries(mappedRows);
        const enrichedRows = mappedRows.map((row) => {
          const summary = errorSummaries[String(row.id)] || null;
          if (!summary) {
            return row;
          }

          return {
            ...row,
            displayEstado: summary.display_status || row.estado,
            hasErrorDetails: Boolean(summary.has_error_details),
            isPartialProcess: Boolean(summary.is_partial),
          };
        });

        setProcessRows(enrichedRows);
      } catch (summaryError) {
        console.error("Error cargando resúmenes de error:", summaryError);
        setProcessRows(mappedRows);
      }
    } catch (err) {
      console.error("Error inesperado al cargar procesos:", err);
      setStatusMessage("Ocurrió un error inesperado al cargar procesos.");
      setStatusType("error");
    } finally {
      setIsLoadingTable(false);
    }
  };

  const checkMercadoLibreConnection = async () => {
    try {
      setIsCheckingMl(true);
      setIsMercadoLibreConnected(false);
      setMlUserId(null);

      const res = await fetch(`${API_BASE}/ml/status`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data?.connected === true) {
        setIsMercadoLibreConnected(true);
        setMlUserId(data?.user_id ? String(data.user_id) : null);
      } else {
        setIsMercadoLibreConnected(false);
        setMlUserId(null);
      }
    } catch (error) {
      console.error("Error verificando conexión con MercadoLibre:", error);
      setIsMercadoLibreConnected(false);
      setMlUserId(null);
    } finally {
      setIsCheckingMl(false);
      setIsConnectingMl(false);
    }
  };

  const loadQueueStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/process-queue/status`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        return null;
      }

      setIsQueueRunning(Boolean(data?.running));
      setQueueButtonText(data?.button_text || "Ejecutar procesos");
      setQueueMessage(data?.message || "");
      setQueueCurrentProcessRowId(data?.current_process_row_id || null);
      setJobProcessedRows(data?.job_processed_rows ?? 0);
      setJobTotalRows(data?.job_total_rows ?? 0);
      return data;
    } catch (error) {
      console.error("Error consultando estado de cola:", error);
      return null;
    }
  };

  const handleConnectMercadoLibre = () => {
    if (isCheckingMl || isMercadoLibreConnected) return;

    setIsConnectingMl(true);
    window.location.href = `${API_BASE}/meli/oauth/start`;
  };

  const handleSaveProcess = async () => {
    if (!isMercadoLibreConnected) {
      setStatusMessage("Primero debes conectar Mercado Libre.");
      setStatusType("error");
      return;
    }

    if (!selectedFile) {
      setStatusMessage("Debes seleccionar un archivo Excel antes de grabar el proceso.");
      setStatusType("error");
      return;
    }

    try {
      setIsSaving(true);
      setStatusMessage("");

      if (!supabase) {
        setStatusMessage("Supabase no está configurado.");
        setStatusType("error");
        return;
      }

      const {
        data: { user },
        error: userError,
      } = await supabase.auth.getUser();

      if (userError || !user?.email) {
        setStatusMessage("No se pudo obtener el usuario autenticado.");
        setStatusType("error");
        return;
      }

      const safeFileName = selectedFile.name.replace(/\s+/g, "_");
      const uniqueFileName = `${Date.now()}_${safeFileName}`;
      const storagePath = `${user.id}/${uniqueFileName}`;
      const bucketName = PROCESS_BUCKET;

      const { error: uploadError } = await supabase.storage
        .from(bucketName)
        .upload(storagePath, selectedFile, {
          cacheControl: "3600",
          upsert: false,
        });

      if (uploadError) {
        setStatusMessage(`No se pudo subir el archivo: ${uploadError.message}`);
        setStatusType("error");
        return;
      }

      const payload = {
        archivo: selectedFile.name,
        fecha_proceso: sqlDate,
        hora_proceso: sqlTime,
        generado: user.email,
        estado: PROCESS_STATUS.PENDING,
        storage_bucket: bucketName,
        storage_path: storagePath,
      };

      const { data, error } = await supabase
        .from("procesos")
        .insert([payload])
        .select("id, proceso_id, archivo, fecha_proceso, hora_proceso, generado, estado, storage_bucket, storage_path")
        .single();

      if (error) {
        await supabase.storage.from(bucketName).remove([storagePath]);
        setStatusMessage(`No se pudo grabar el proceso: ${error.message}`);
        setStatusType("error");
        return;
      }

      setProcessRows((prev) => [mapProcessRow(data), ...prev]);
      setSelectedFile(null);
      setStatusMessage("Proceso guardado correctamente.");
      setStatusType("success");

      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (err) {
      console.error("Error inesperado al grabar proceso:", err);
      setStatusMessage("Ocurrió un error inesperado al grabar el proceso.");
      setStatusType("error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleGenerateProcesses = async () => {
    if (!isMercadoLibreConnected) {
      setStatusMessage("Primero debes conectar Mercado Libre.");
      setStatusType("error");
      return;
    }

    if (!mlUserId) {
      setStatusMessage("No se encontró user_id asociado a la conexión de Mercado Libre.");
      setStatusType("error");
      return;
    }

    if (isQueueRunning) {
      setStatusMessage("Ya existe una cola de procesos en ejecución.");
      setStatusType("info");
      return;
    }

    if (!hasPendingProcesses) {
      setStatusMessage("No hay procesos pendientes para ejecutar.");
      setStatusType("info");
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/process-queue/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          user_id: String(mlUserId),
        }),
      });

      const data = await res.json().catch(() => ({}));

      if (res.status === 409) {
        await loadQueueStatus();
        setStatusMessage(data?.detail || "Ya existe una cola de procesos en ejecución.");
        setStatusType("info");
        return;
      }

      if (!res.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "No se pudo iniciar la cola de procesos."
        );
      }

      await loadQueueStatus();
      await loadProcesses();
      setStatusMessage("Cola de procesos iniciada correctamente.");
      setStatusType("success");
    } catch (error) {
      setStatusMessage(
        error?.message || "Ocurrió un error al iniciar la cola de procesos."
      );
      setStatusType("error");
    }
  };

  const handleOpenErrorModal = async (row) => {
    setSelectedErrorRow(row);
    setSelectedErrorDetails(null);
    setErrorDetailsLoadMessage("");
    setIsLoadingErrorDetails(true);

    try {
      const res = await fetch(`${API_BASE}/process-queue/errors/${row.id}`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "No se pudieron obtener los detalles del error."
        );
      }

      setSelectedErrorDetails(data);
    } catch (error) {
      setErrorDetailsLoadMessage(
        error?.message ||
          "No se pudieron obtener los detalles del error."
      );
    } finally {
      setIsLoadingErrorDetails(false);
    }
  };

  const handleCloseErrorModal = () => {
    setSelectedErrorRow(null);
    setSelectedErrorDetails(null);
    setIsLoadingErrorDetails(false);
    setErrorDetailsLoadMessage("");
  };

  const getStatusClass = (status) => {
    const normalized = String(status || "").toLowerCase();

    if (normalized === PROCESS_STATUS.PROCESSED_WITH_ERRORS.toLowerCase()) {
      return "status-badge status-badge--success";
    }

    if (
      normalized === PROCESS_STATUS.PROCESSED.toLowerCase() ||
      normalized === "completado"
    ) {
      return "status-badge status-badge--success";
    }

    if (normalized === PROCESS_STATUS.PROCESSING.toLowerCase()) {
      return "status-badge status-badge--info";
    }

    if (normalized === PROCESS_STATUS.PENDING.toLowerCase()) {
      return "status-badge status-badge--warning";
    }

    if (normalized === PROCESS_STATUS.ERROR.toLowerCase()) {
      return "status-badge status-badge--danger";
    }

    return "status-badge";
  };

  const mlStatusText = isCheckingMl
    ? "Verificando conexión con Mercado Libre..."
    : isMercadoLibreConnected
    ? `Conectado${mlUserId ? ` · user_id ${mlUserId}` : ""}`
    : "No conectado";

  return (
    <section className="main-sync-jobs">
      <div className="main-sync-jobs__card">
        {!isMercadoLibreConnected && (
          <div className="main-sync-jobs__topbar">
            <div className="main-sync-jobs__connection">
              <span className="main-sync-jobs__connection-badge main-sync-jobs__connection-badge--pending">
                {mlStatusText}
              </span>

              <button
                type="button"
                className="main-sync-jobs__connect-button"
                onClick={handleConnectMercadoLibre}
                disabled={isCheckingMl || isConnectingMl}
              >
                {isCheckingMl
                  ? "Verificando..."
                  : isConnectingMl
                  ? "Conectando..."
                  : "Conectar Mercado Libre"}
              </button>
            </div>
          </div>
        )}

        <div className="main-sync-jobs__row">
          <div className="main-sync-jobs__field main-sync-jobs__field--file">
            <span className="main-sync-jobs__label">Archivo del proceso</span>

            <label className="main-sync-jobs__file-box">
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls,.csv"
                className="main-sync-jobs__file-input"
                onChange={handleFileChange}
              />
              <span className="main-sync-jobs__file-button">
                Seleccionar archivo
              </span>
              <span className="main-sync-jobs__file-name">
                {selectedFile?.name || "No hay archivo seleccionado"}
              </span>
            </label>
          </div>

          <div className="main-sync-jobs__field">
            <span className="main-sync-jobs__label">Fecha</span>
            <div className="main-sync-jobs__info-box">{formattedDate}</div>
          </div>

          <div className="main-sync-jobs__field">
            <span className="main-sync-jobs__label">Hora</span>
            <div className="main-sync-jobs__info-box">{formattedTime}</div>
          </div>
        </div>

        {statusMessage && (
          <p
            className={`main-sync-jobs__message main-sync-jobs__message--${statusType}`}
          >
            {statusMessage}
          </p>
        )}

        <div className="main-sync-jobs__actions">
          <button
            type="button"
            className="main-sync-jobs__save-button"
            onClick={handleSaveProcess}
            disabled={isSaving || !selectedFile}
          >
            {isSaving ? "Guardando..." : "Guardar proceso"}
          </button>

          <button
            type="button"
            className="main-sync-jobs__generate-button"
            onClick={handleGenerateProcesses}
            disabled={
              isQueueRunning || !isMercadoLibreConnected || !hasPendingProcesses
            }
          >
            {isQueueRunning
              ? queueButtonText || "Procesos en ejecución"
              : "Ejecutar procesos"}
          </button>
        </div>

        {visibleQueueMessage && (
          <p className="main-sync-jobs__queue-message">{visibleQueueMessage}</p>
        )}
      </div>

      <div className="main-sync-jobs__table-card">
        <div className="main-sync-jobs__table-header">
          <h2 className="main-sync-jobs__table-title">
            Procesos cargados
          </h2>
        </div>

        <div className="main-sync-jobs__table-scroll">
          <table className="process-table">
            <thead>
              <tr>
                <th className="process-table__action-header" aria-label="Error" />
                <th>Archivo</th>
                <th>Fecha proceso</th>
                <th>Generado por</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {isLoadingTable ? (
                <tr>
                  <td colSpan="5" className="main-sync-jobs__empty-row">
                    Cargando procesos...
                  </td>
                </tr>
              ) : processRows.length === 0 ? (
                <tr>
                  <td colSpan="5" className="main-sync-jobs__empty-row">
                    No hay procesos registrados todavía.
                  </td>
                </tr>
              ) : (
                processRows.map((row) => {
                  const isCurrentlyProcessing =
                    isQueueRunning && queueCurrentProcessRowId === row.id;
                  const displayStatus = isCurrentlyProcessing
                    ? PROCESS_STATUS.PROCESSING
                    : row.displayEstado || row.estado;
                  const hasProcessError = Boolean(row.hasErrorDetails);
                  const isPartialProcess = Boolean(row.isPartialProcess);
                  const showProgressBar =
                    isCurrentlyProcessing && jobTotalRows > 0;
                  const progressPercent = showProgressBar
                    ? Math.min(
                        100,
                        Math.round((jobProcessedRows / jobTotalRows) * 100)
                      )
                    : 0;

                  return (
                    <tr key={row.id}>
                      <td className="process-table__action-cell">
                        {hasProcessError ? (
                          <button
                            type="button"
                            className={`process-table__error-button ${
                              isPartialProcess
                                ? "process-table__error-button--partial"
                                : ""
                            }`}
                            onClick={() => handleOpenErrorModal(row)}
                            aria-label={`Ver detalle del proceso ${row.archivo}`}
                            title="Ver detalle del proceso"
                          >
                            <svg
                              viewBox="0 0 24 24"
                              aria-hidden="true"
                              className="process-table__error-icon"
                            >
                              <circle
                                cx="12"
                                cy="12"
                                r="9"
                                className="process-table__error-icon-ring"
                              />
                              <path
                                d="M8.5 8.5 15.5 15.5"
                                className="process-table__error-icon-cross"
                              />
                              <path
                                d="M15.5 8.5 8.5 15.5"
                                className="process-table__error-icon-cross"
                              />
                            </svg>
                          </button>
                        ) : (
                          <span
                            className="process-table__error-placeholder"
                            aria-hidden="true"
                          />
                        )}
                      </td>
                      <td>
                        <span className="process-table__file-name">
                          {row.archivo}
                        </span>
                      </td>
                      <td>{row.fecha}</td>
                      <td>{row.procesadoPor}</td>
                      <td>
                        <div className="status-cell">
                          <span className={getStatusClass(displayStatus)}>
                            {displayStatus}
                          </span>
                          {showProgressBar && (
                            <div className="progress-container">
                              <div className="progress-bar">
                                <div
                                  className="progress-bar__fill"
                                  style={{ width: `${progressPercent}%` }}
                                />
                              </div>
                              <span className="progress-label">
                                {jobProcessedRows} / {jobTotalRows}
                              </span>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <ProcessQueueErrorModal
        row={selectedErrorRow}
        errorData={selectedErrorDetails}
        isLoading={isLoadingErrorDetails}
        loadError={errorDetailsLoadMessage}
        onClose={handleCloseErrorModal}
      />
    </section>
  );
}
