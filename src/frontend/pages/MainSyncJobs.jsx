import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  FileSpreadsheet,
  LoaderCircle,
  RefreshCw,
  Save,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import ProcessQueueErrorModal from "../components/ProcessQueueErrorModal.jsx";
import ProcessBoard from "../components/ProcessBoard.jsx";
import "../styles/MainSyncJobs.css";
import { supabase } from "../../lib/supabase.js";
import { authFetch } from "../../lib/apiClient.js";
import { sanitizeStorageFileName } from "../utils/storageFileName.js";
import { PROCESS_STATUS } from "../utils/processBoard.js";
import { processQueueRevision } from "../utils/processPages.js";
import { createVisiblePoller, pollingError } from "../utils/visiblePolling.js";
import useProcessPages from "../hooks/useProcessPages.js";

const SYNC_ROUTE = "/procesos/sincronizacion-procesos";
const PROCESS_BUCKET = "excel-procesos";

export default function MainSyncJobs() {
  const fileInputRef = useRef(null);

  const [selectedFile, setSelectedFile] = useState(null);
  const [now, setNow] = useState(new Date());
  const [boardRevision, setBoardRevision] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [statusType, setStatusType] = useState("info");
  const [isQueueRunning, setIsQueueRunning] = useState(false);
  const [queueButtonText, setQueueButtonText] = useState("Ejecutar procesos");
  const [queueMessage, setQueueMessage] = useState("");
  const [queueStatusError, setQueueStatusError] = useState("");
  const [queueCurrentProcessRowId, setQueueCurrentProcessRowId] = useState(null);
  const [queueCurrentProcessType, setQueueCurrentProcessType] = useState(null);
  const [jobMessage, setJobMessage] = useState("");
  const [jobProcessedRows, setJobProcessedRows] = useState(0);
  const [jobTotalRows, setJobTotalRows] = useState(0);
  const [jobCompatibilitiesCreated, setJobCompatibilitiesCreated] = useState(0);
  const [selectedErrorRow, setSelectedErrorRow] = useState(null);
  const [selectedErrorDetails, setSelectedErrorDetails] = useState(null);
  const [isLoadingErrorDetails, setIsLoadingErrorDetails] = useState(false);
  const [errorDetailsLoadMessage, setErrorDetailsLoadMessage] = useState("");
  const [exportingRowId, setExportingRowId] = useState(null);

  const [isCheckingMl, setIsCheckingMl] = useState(true);
  const [isMercadoLibreConnected, setIsMercadoLibreConnected] = useState(false);
  const [mlUserId, setMlUserId] = useState(null);

  const location = useLocation();
  const navigate = useNavigate();

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  const checkMercadoLibreConnection = useCallback(async () => {
    try {
      setIsCheckingMl(true);
      setIsMercadoLibreConnected(false);
      setMlUserId(null);

      const res = await authFetch(`${API_BASE}/ml/status`, {
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
    }
  }, [API_BASE]);

  useEffect(() => {
    const interval = setInterval(() => {
      setNow(new Date());
    }, 1000);

    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    checkMercadoLibreConnection();
  }, [checkMercadoLibreConnection]);

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
  }, [location.search, navigate, checkMercadoLibreConnection]);

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

  const { lanes, reload: loadProcesses } = useProcessPages({
    revision: boardRevision,
    currentId: isQueueRunning ? queueCurrentProcessRowId : null,
    running: isQueueRunning,
  });
  const processGroups = {
    processing: lanes.processing.rows,
    pending: lanes.pending.rows,
    completed: lanes.completed.rows,
  };
  const isLoadingProcesses = Object.values(lanes).some((lane) => lane.loading);
  const historyError = Object.values(lanes).find((lane) => lane.error)?.error || "";
  const pendingProcessCount = lanes.pending.total;

  const hasPendingProcesses = pendingProcessCount > 0;

  const visibleQueueMessage = useMemo(() => {
    if (queueStatusError) return queueStatusError;
    if (isQueueRunning) {
      return jobMessage || queueMessage;
    }

    if (hasPendingProcesses) {
      return pendingProcessCount === 1
        ? "Hay 1 proceso pendiente listo para ejecutar."
        : `Hay ${pendingProcessCount} procesos pendientes listos para ejecutar.`;
    }

    return queueMessage;
  }, [
    hasPendingProcesses,
    isQueueRunning,
    jobMessage,
    pendingProcessCount,
    queueMessage,
    queueStatusError,
  ]);

  const handleFileChange = (event) => {
    const file = event.target.files?.[0] || null;
    setSelectedFile(file);
    setStatusMessage("");
    setStatusType("info");
  };

  const loadQueueStatus = useCallback(async (signal) => {
    const res = await authFetch(`${API_BASE}/process-queue/status`, {
      method: "GET", credentials: "include", signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw pollingError(res, data.detail || "No se pudo consultar la cola. Actualiza la página para reintentar.");
    }
    if (signal?.aborted) return data;
    setQueueStatusError("");
    setIsQueueRunning(Boolean(data?.running));
    setQueueButtonText(data?.button_text || "Ejecutar procesos");
    setQueueMessage(data?.message || "");
    setQueueCurrentProcessRowId(data?.current_process_row_id || null);
    setQueueCurrentProcessType(data?.current_process_type || null);
    setJobMessage(data?.job_message || "");
    setJobProcessedRows(data?.job_processed_rows ?? 0);
    setJobTotalRows(data?.job_total_rows ?? 0);
    setJobCompatibilitiesCreated(data?.job_compatibilities_created ?? 0);
    setBoardRevision(processQueueRevision(data));
    return data;
  }, [API_BASE]);

  useEffect(() => createVisiblePoller({
    run: async (signal) => {
      const data = await loadQueueStatus(signal);
      return data.running ? 5000 : 30000;
    },
    onError: (error) => {
      setQueueStatusError(error.message + (error.pollingStopped ? " Actualiza la página para reintentar el seguimiento." : ""));
    },
  }), [loadQueueStatus]);

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

      const safeFileName = sanitizeStorageFileName(selectedFile.name);
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

      const { error } = await supabase
        .from("procesos")
        .insert([payload]);

      if (error) {
        await supabase.storage.from(bucketName).remove([storagePath]);
        setStatusMessage(`No se pudo grabar el proceso: ${error.message}`);
        setStatusType("error");
        return;
      }

      lanes.pending.setPage(1);
      loadProcesses();
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
      const res = await authFetch(`${API_BASE}/process-queue/start`, {
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

      setJobMessage("");
      setJobProcessedRows(0);
      setJobTotalRows(0);
      setJobCompatibilitiesCreated(0);
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
      const res = await authFetch(`${API_BASE}/process-queue/errors/${row.id}`, {
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

  const handleDownloadProcessResult = async (row) => {
    if (!row?.id || exportingRowId === row.id) {
      return;
    }

    try {
      setExportingRowId(row.id);
      setStatusMessage("");

      const response = await authFetch(
        `${API_BASE}/process-queue/results/${row.id}/export`,
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
            "No se pudo descargar el archivo de resultado."
        );
      }

      const blob = await response.blob();
      const contentDisposition =
        response.headers.get("Content-Disposition") || "";
      const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/i);
      const filename =
        filenameMatch?.[1] || `resultado_proceso_${String(row.id)}.xlsx`;
      const downloadUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = downloadUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      setStatusMessage(
        error?.message || "No se pudo descargar el archivo de resultado."
      );
      setStatusType("error");
    } finally {
      setExportingRowId(null);
    }
  };

  const mlStatusText = isCheckingMl
    ? "Verificando conexión con Mercado Libre..."
    : isMercadoLibreConnected
    ? `Conectado${mlUserId ? ` · user_id ${mlUserId}` : ""}`
    : "No conectado";

  return (
    <section className="main-sync-jobs">
      {(isCheckingMl || !isMercadoLibreConnected) && (
        <div
          className="main-sync-jobs__ml-modal-backdrop"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="ml-connection-modal-title"
          aria-describedby="ml-connection-modal-description"
        >
          <div className="main-sync-jobs__ml-modal">
            {isCheckingMl ? (
              <>
                <div className="main-sync-jobs__ml-modal-icon main-sync-jobs__ml-modal-icon--loading">
                  <LoaderCircle aria-hidden="true" />
                </div>
                <h2 id="ml-connection-modal-title">
                  Verificando conexion con Mercado Libre
                </h2>
                <p id="ml-connection-modal-description">
                  Estamos validando la conexion antes de habilitar la
                  sincronizacion de procesos.
                </p>
              </>
            ) : (
              <>
                <div className="main-sync-jobs__ml-modal-icon main-sync-jobs__ml-modal-icon--error">
                  <AlertTriangle aria-hidden="true" />
                </div>
                <h2 id="ml-connection-modal-title">
                  Mercado Libre no esta conectado
                </h2>
                <p id="ml-connection-modal-description">
                  Contacte con administrador para conectar a Mercado Libre.
                </p>
                <button
                  type="button"
                  className="main-sync-jobs__ml-modal-button"
                  onClick={checkMercadoLibreConnection}
                >
                  <RefreshCw aria-hidden="true" />
                  Reintentar verificacion
                </button>
              </>
            )}
          </div>
        </div>
      )}

      <header className="main-sync-jobs__page-header">
        <h1>Sincronización de Procesos</h1>
        <p>Gestiona archivos, cola de ejecución y resultados de procesos masivos.</p>
      </header>

      <div className="main-sync-jobs__card">
        <header className="main-sync-jobs__card-header">
          <div className="main-sync-jobs__card-heading">
            <span className="main-sync-jobs__card-icon">
              <FileSpreadsheet size={21} aria-hidden="true" />
            </span>
            <h2>Carga y ejecución</h2>
            <span className={`main-sync-jobs__queue-status status-badge status-badge--${isQueueRunning ? "info" : "neutral"}`}>
              {isQueueRunning ? "Cola en ejecución" : "Motor disponible"}
            </span>
          </div>
        </header>

        {!isMercadoLibreConnected && (
          <div className="main-sync-jobs__topbar">
            <div className="main-sync-jobs__connection">
              <span className="main-sync-jobs__connection-badge main-sync-jobs__connection-badge--pending">
                {mlStatusText}
              </span>

              <button
                type="button"
                className="main-sync-jobs__connect-button"
                onClick={checkMercadoLibreConnection}
                disabled={isCheckingMl}
              >
                {isCheckingMl
                  ? "Verificando..."
                  : "Conectar Mercado Libre"}
              </button>
            </div>
          </div>
        )}

        <div className="main-sync-jobs__workspace">
          <div className="main-sync-jobs__process-panel">
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
                  <span className="main-sync-jobs__file-name" title={selectedFile?.name}>
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
          </div>

          <div className="main-sync-jobs__actions" aria-label="Acciones de procesos">
            <button
              type="button"
              className="main-sync-jobs__action-button main-sync-jobs__action-button--save"
              onClick={handleSaveProcess}
              disabled={isSaving || !selectedFile}
            >
              <Save size={18} aria-hidden="true" />
              <span>{isSaving ? "Guardando..." : "Guardar proceso"}</span>
            </button>

            <button
              type="button"
              className="main-sync-jobs__action-button main-sync-jobs__action-button--queue"
              title={isQueueRunning ? queueButtonText || "Procesos en ejecución" : "Ejecutar procesos"}
              onClick={handleGenerateProcesses}
              disabled={
                isQueueRunning || !isMercadoLibreConnected || !hasPendingProcesses
              }
            >
              <BarChart3 size={18} aria-hidden="true" />
              <span>
                {isQueueRunning
                  ? queueButtonText || "Procesos en ejecución"
                  : "Ejecutar procesos"}
              </span>
            </button>
          </div>
        </div>

        {(statusMessage || visibleQueueMessage) && (
          <div className="main-sync-jobs__feedback" role="status">
            {statusMessage && (
              <p className={`main-sync-jobs__message main-sync-jobs__message--${statusType}`} title={statusMessage}>
                {statusMessage}
              </p>
            )}
            {visibleQueueMessage && (
              <p className="main-sync-jobs__queue-message" title={visibleQueueMessage}>{visibleQueueMessage}</p>
            )}
          </div>
        )}
      </div>

      {historyError && (
        <p className="main-sync-jobs__message main-sync-jobs__message--error" role="alert">
          {historyError}
          <button type="button" onClick={loadProcesses}>Actualizar historial</button>
        </p>
      )}

      <ProcessBoard
        groups={processGroups}
        pagination={lanes}
        isLoading={isLoadingProcesses}
        isQueueRunning={isQueueRunning}
        currentProcessRowId={queueCurrentProcessRowId}
        currentProcessType={queueCurrentProcessType}
        jobMessage={jobMessage}
        jobProcessedRows={jobProcessedRows}
        jobTotalRows={jobTotalRows}
        jobCompatibilitiesCreated={jobCompatibilitiesCreated}
        exportingRowId={exportingRowId}
        onOpenError={handleOpenErrorModal}
        onDownloadResult={handleDownloadProcessResult}
      />

      <ProcessQueueErrorModal
        row={selectedErrorRow}
        apiBase={API_BASE}
        errorData={selectedErrorDetails}
        isLoading={isLoadingErrorDetails}
        loadError={errorDetailsLoadMessage}
        onClose={handleCloseErrorModal}
      />
    </section>
  );
}
