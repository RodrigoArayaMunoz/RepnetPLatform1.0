import "../styles/NoCompatibilitiesUpload.css";
import { useEffect, useState, useRef } from "react";
import ResultModal from "../components/ResultModal";
import ResultViewNoCompatibilities from "../components/ResultViewNoCompatibilities";

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

  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");

  const [mlConnected, setMlConnected] = useState(false);
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

  useEffect(() => {
    checkMlConnection();
  }, []);

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

  const checkMlConnection = async () => {
    try {
      setCheckingConnection(true);
      setMlVerified(false);
      setMlConnected(false);
      setMlUserId(null);
      setMlStatusMessage("Verificando conexión con Mercado Libre...");

      const res = await fetch(`${API_BASE}/ml/status`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data?.connected === true) {
        setMlConnected(true);
        setMlVerified(true);
        setMlUserId(data?.user_id ? String(data.user_id) : null);
        setMlStatusMessage("Conectado exitosamente");
      } else {
        setMlConnected(false);
        setMlVerified(false);
        setMlUserId(null);
        setMlStatusMessage("Debes conectar tu cuenta de Mercado Libre");
      }
    } catch (error) {
      setMlConnected(false);
      setMlVerified(false);
      setMlUserId(null);
      setMlStatusMessage("No se pudo verificar la conexión con Mercado Libre");
    } finally {
      setCheckingConnection(false);
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

    const res = await fetch(
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

  const fetchJobResult = async (currentJobId) => {
    const res = await fetch(`${API_BASE}/imports/${currentJobId}/result`, {
      method: "GET",
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data?.detail || data?.message || "No se pudo obtener el resultado final."
      );
    }

    setJobResult(data);
    setShowResultModal(true);
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const pollStageJob = async (currentJobId, stageLabel) => {
    let finished = false;

    while (!finished) {
      const response = await fetch(`${API_BASE}/imports/${currentJobId}`, {
        credentials: "include",
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data?.detail || data?.message || "Error consultando el estado del proceso."
        );
      }

      const currentProgress =
        typeof data.progress === "number" ? data.progress : 0;

      setProgress(Math.min(100, currentProgress));
      setProcessMessage(`${stageLabel}: ${data.message || "Procesando..."}`);
      setMessage(data.message || "");

      if (data.status === "success") {
        finished = true;
        return data;
      }

      if (data.status === "error") {
        throw new Error(data.message || `Error en etapa ${stageLabel}`);
      }

      await sleep(1200);
    }
  };

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
      setExceptionJobId(newExceptionJobId);

      await pollStageJob(
        newExceptionJobId,
        "Informando excepciones por MLC"
      );

      setProgress(100);
      setStatus("success");

      try {
        setLoadingResult(true);
        await fetchJobResult(newExceptionJobId);
      } catch (error) {
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
      setLoadingProcess(false);
      setLoadingResult(false);
      setStatus("error");
      setMessage(error?.message || "Ocurrió un error al procesar el archivo.");
    }
  };

  const handleConnectMercadoLibre = () => {
    if (checkingConnection || mlVerified) return;
    const redirectTo = `${window.location.pathname}${window.location.search}`;
    window.location.href = `${API_BASE}/auth/login?redirect_to=${encodeURIComponent(
      redirectTo
    )}`;
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
