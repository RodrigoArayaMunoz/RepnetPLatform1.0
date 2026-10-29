import "../styles/NoCompatibilitiesUpload.css";
import { useCallback, useEffect, useState, useRef } from "react";
import ResultModal from "../components/ResultModal";
import ResultViewNoCompatibilities from "../components/ResultViewNoCompatibilities";
import { authFetch, startMercadoLibreLogin } from "../../lib/apiClient.js";
import { pollingError, waitForVisibleJob } from "../utils/visiblePolling.js";

function ProcessingOverlay({ visible, progress = 0, message = "" }) {
  if (!visible) return null;

  return (
    <div className="processing-overlay">
      <div className="processing-box">
        <div className="processing-spinner" />
        <h2>Informando No Compatibilidades</h2>
        <p className="processing-progress">{progress}%</p>
        <p className="processing-message">
          {message || "Procesando archivo..."}
        </p>

        <div className="processing-bar">
          <div
            className="processing-bar-fill"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
    </div>
  );
}

function NoCompatibilitiesUpload() {
  const fileInputRef = useRef(null);
  const observerRef = useRef(null);
  useEffect(() => () => observerRef.current?.abort(), []);

  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");

  const [mlVerified, setMlVerified] = useState(false);
  const [mlUserId, setMlUserId] = useState(null);
  const [checkingConnection, setCheckingConnection] = useState(true);
  const [mlStatusMessage, setMlStatusMessage] = useState(
    "Verificando conexión con Mercado Libre..."
  );

  const [jobResult, setJobResult] = useState(null);
  const [loadingResult, setLoadingResult] = useState(false);
  const [exceptionJobId, setExceptionJobId] = useState(null);
  const [loadingProcess, setLoadingProcess] = useState(false);
  const [progress, setProgress] = useState(0);
  const [processMessage, setProcessMessage] = useState("");
  const [showResultModal, setShowResultModal] = useState(false);
  const [showPublicationsModal, setShowPublicationsModal] = useState(false);

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  const checkMlConnection = useCallback(async () => {
    try {
      setCheckingConnection(true);
      setMlVerified(false);
      setMlUserId(null);
      setMlStatusMessage("Verificando conexión con Mercado Libre...");

      const res = await authFetch(`${API_BASE}/ml/status`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data?.connected === true) {
        setMlVerified(true);
        setMlUserId(data?.user_id ? String(data.user_id) : null);
        setMlStatusMessage("Conectado exitosamente");
      } else {
        setMlVerified(false);
        setMlUserId(null);
        setMlStatusMessage("Debes conectar tu cuenta de Mercado Libre");
      }
    } catch {
      setMlVerified(false);
      setMlUserId(null);
      setMlStatusMessage("No se pudo verificar la conexión con Mercado Libre");
    } finally {
      setCheckingConnection(false);
    }
  }, [API_BASE]);

  useEffect(() => {
    checkMlConnection();
  }, [checkMlConnection]);

  const handleCloseResultModal = () => {
    setShowResultModal(false);
    setFile(null);
    setExceptionJobId(null);
    setJobResult(null);
    setProgress(0);
    setProcessMessage("");
    setStatus("idle");
    setMessage("");

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const isExcelFile = (selectedFile) => {
    if (!selectedFile) return false;

    const name = selectedFile.name?.toLowerCase() || "";
    const validExtension =
      name.endsWith(".xlsx") ||
      name.endsWith(".xls") ||
      name.endsWith(".csv");

    const validMime =
      selectedFile.type ===
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
      selectedFile.type === "application/vnd.ms-excel" ||
      selectedFile.type === "text/csv" ||
      selectedFile.type === "application/csv" ||
      selectedFile.type === "" ||
      selectedFile.type === "application/octet-stream";

    return validExtension && validMime;
  };

  const handleFileChange = (e) => {
    if (!mlVerified) return;

    const selectedFile = e.target.files?.[0];
    if (!selectedFile) return;

    if (!isExcelFile(selectedFile)) {
      setFile(null);
      setExceptionJobId(null);
      setStatus("error");
      setMessage("Archivo no válido. Selecciona un Excel (.xlsx, .xls) o CSV (.csv).");
      return;
    }

    setFile(selectedFile);
    setExceptionJobId(null);
    setStatus("idle");
    setMessage("");
    setJobResult(null);
    setShowResultModal(false);
    setProgress(0);
    setProcessMessage("");
  };

  const startCompatibilityExceptionsJob = async (fileToUpload) => {
    if (!mlUserId) {
      throw new Error("No se encontró user_id de Mercado Libre conectado.");
    }

    const formData = new FormData();
    formData.append("file", fileToUpload);

    const res = await authFetch(
      `${API_BASE}/imports/compatibility-exceptions?user_id=${encodeURIComponent(mlUserId)}`,
      {
        method: "POST",
        body: formData,
        credentials: "include",
      }
    );

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data?.detail ||
          data?.message ||
          "No se pudo iniciar el proceso de excepciones de compatibilidad."
      );
    }

    if (!data?.job_id) {
      throw new Error("No se recibió job_id del proceso de excepciones.");
    }

    return data.job_id;
  };

  const fetchJobResult = async (currentJobId, signal) => {
    const res = await authFetch(`${API_BASE}/imports/${currentJobId}/result`, {
      method: "GET", signal,
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data?.detail || data?.message || "No se pudo obtener el resultado final."
      );
    }

    if (signal?.aborted) return;
    setJobResult(data);
    setShowResultModal(true);
  };

  const pollStageJob = (currentJobId, stageLabel, signal) => waitForVisibleJob({
    signal,
    read: async (requestSignal) => {
      const response = await authFetch(`${API_BASE}/imports/${currentJobId}`, {
        credentials: "include", signal: requestSignal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw pollingError(response,
          data?.detail || data?.message || "No se pudo consultar el avance. El proceso puede seguir ejecutándose.",
        );
      }
      return data;
    },
    onProgress: (data) => {
      setProgress(Math.min(100, typeof data.progress === "number" ? data.progress : 0));
      setProcessMessage(`${stageLabel}: ${data.message || "Procesando..."}`);
      setMessage(data.message || "");
    },
  });

  const handleProcess = async () => {
    if (!mlVerified) {
      setStatus("error");
      setMessage("Primero debes conectar tu cuenta de Mercado Libre.");
      return;
    }

    if (!mlUserId) {
      setStatus("error");
      setMessage("No se encontró user_id asociado a la conexión de Mercado Libre.");
      return;
    }

    if (!file) {
      setStatus("error");
      setMessage("Debes seleccionar un archivo antes de iniciar el proceso.");
      return;
    }

    const observer = new AbortController();
    observerRef.current?.abort();
    observerRef.current = observer;
    try {
      setShowResultModal(false);
      setJobResult(null);
      setStatus("processing");
      setLoadingProcess(true);
      setLoadingResult(false);
      setProgress(0);
      setMessage("");
      setProcessMessage("Iniciando proceso de excepciones...");

      const newExceptionJobId = await startCompatibilityExceptionsJob(file);
      if (observer.signal.aborted) return;
      setExceptionJobId(newExceptionJobId);

      await pollStageJob(
        newExceptionJobId,
        "Informando excepciones por MLC", observer.signal
      );

      setProgress(100);
      setStatus("success");

      try {
        setLoadingResult(true);
        await fetchJobResult(newExceptionJobId, observer.signal);
      } catch (error) {
        if (observer.signal.aborted) return;
        setStatus("error");
        setMessage(
          error?.message ||
            "El proceso terminó, pero no se pudo obtener el resumen."
        );
      } finally {
        setLoadingResult(false);
        setLoadingProcess(false);
      }
    } catch (error) {
      if (observer.signal.aborted) return;
      setLoadingProcess(false);
      setLoadingResult(false);
      setStatus("error");
      setMessage(error?.message || "Ocurrió un error al procesar el archivo.");
    } finally {
      if (observerRef.current === observer) observerRef.current = null;
    }
  };

  const handleConnectMercadoLibre = async () => {
    if (checkingConnection || mlVerified) return;
    const redirectTo = `${window.location.pathname}${window.location.search}`;
    try {
      await startMercadoLibreLogin(API_BASE, redirectTo);
    } catch (error) {
      setStatus("error");
      setMessage(
        error?.message || "No se pudo iniciar la conexion con Mercado Libre."
      );
    }
  };

  const acceptText = "Archivo permitido: .xlsx, .xls o .csv con columna MLC";
  const buttonText =
    status === "processing" ? "Procesando..." : "Informar Excepciones";

  const connectButtonText = checkingConnection
    ? "Verificando conexión..."
    : mlVerified
    ? "Cuenta conectada"
    : "Conectar con MercadoLibre";

  const statusText = checkingConnection
    ? "Verificando conexión con Mercado Libre..."
    : mlVerified
    ? "Conectado exitosamente"
    : mlStatusMessage;

  const handleViewPublicationsWithoutCompatibilities = () => {
    setShowPublicationsModal(true);
  };

  const handleClosePublicationsModal = () => {
    setShowPublicationsModal(false);
  };

  return (
    <>
      <ProcessingOverlay
        visible={loadingProcess}
        progress={progress}
        message={processMessage}
      />

      <section className="compat-page">
        <div className="compat-upload-layout">
          <header className="compat-upload-header">
            <h1>No Compatibilidades</h1>
            <p>Informa publicaciones sin compatibilidades desde un archivo Excel o CSV.</p>
          </header>

          <div className="compat-upload-card compat-upload-card--single">
          <div className="ml-connection-block">
            <button
              className={`process-button-ml ${mlVerified ? "connected" : ""}`}
              onClick={handleConnectMercadoLibre}
              disabled={checkingConnection || mlVerified}
              type="button"
            >
              {connectButtonText}
            </button>

            <p className={`ml-status ${mlVerified ? "success" : "pending"}`}>
              {statusText}
            </p>
          </div>

          <div className={`file-wrapper ${!mlVerified ? "disabled-section" : ""}`}>
            <label
              className={`file-label ${!mlVerified ? "disabled-label" : ""}`}
              htmlFor="fileInput"
            >
              Seleccionar archivo con columna MLC
            </label>

            <input
              ref={fileInputRef}
              id="fileInput"
              className="file-input"
              type="file"
              accept=".xlsx,.xls,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
              onChange={handleFileChange}
              disabled={!mlVerified || status === "processing" || checkingConnection}
            />

            <span className="file-name">
              {file ? file.name : "Ningún archivo seleccionado"}
            </span>

            <small className="file-help-text">{acceptText}</small>
          </div>

          <div className="actions-row">
            <button
              className="process-button"
              onClick={handleProcess}
              disabled={
                !mlVerified ||
                !mlUserId ||
                !file ||
                status === "processing" ||
                checkingConnection ||
                loadingResult ||
                loadingProcess
              }
              type="button"
            >
              {loadingResult
                ? "Cargando resumen..."
                : loadingProcess
                ? "Procesando..."
                : buttonText}
            </button>

            <button
              className="process-button secondary-action-button"
              onClick={handleViewPublicationsWithoutCompatibilities}
              disabled={!mlVerified || checkingConnection || loadingProcess || loadingResult}
              type="button"
            >
              Ver Publicaciones sin compatibilidades
            </button>
          </div>

          {message && !loadingProcess && (
            <p className={`status-message ${status}`}>{message}</p>
          )}

          {exceptionJobId && (
            <div className="job-debug-info">
              <p>Job excepciones: {exceptionJobId}</p>
            </div>
          )}
          </div>
        </div>
      </section>

      <ResultModal
        open={showResultModal}
        onClose={handleCloseResultModal}
        summary={jobResult?.summary}
        results={jobResult?.results}
      />

      <ResultViewNoCompatibilities
        open={showPublicationsModal}
        onClose={handleClosePublicationsModal}
        apiBase={API_BASE}
      />
    </>
  );
}

export default NoCompatibilitiesUpload;
