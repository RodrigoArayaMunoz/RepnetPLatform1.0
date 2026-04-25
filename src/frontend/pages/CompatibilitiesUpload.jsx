import "../styles/CompatibilitiesUpload.css";
import { useEffect, useState, useRef } from "react";
import ResultModal from "../components/ResultModal";
import PublicationsWithoutCompatibilityModal from "../components/PublicationsWithoutCompatibilityModal";
import {
  ML_VERIFYING_MESSAGE,
  readMlConnectionStatus,
} from "../../lib/meliConnection.js";

function ProcessingOverlay({ visible, progress = 0, message = "" }) {
  if (!visible) return null;

  return (
    <div className="processing-overlay">
      <div className="processing-box">
        <div className="processing-spinner" />
        <h2>Procesando compatibilidades</h2>
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

function CompatibilitiesUpload() {
  const fileInputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");

  const [mlConnected, setMlConnected] = useState(false);
  const [mlVerified, setMlVerified] = useState(false);
  const [mlUserId, setMlUserId] = useState(null);
  const [checkingConnection, setCheckingConnection] = useState(true);
  const [mlStatusMessage, setMlStatusMessage] = useState(ML_VERIFYING_MESSAGE);

  const [jobResult, setJobResult] = useState(null);
  const [loadingResult, setLoadingResult] = useState(false);

  const [resolveJobId, setResolveJobId] = useState(null);
  const [batchJobId, setBatchJobId] = useState(null);

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
    setResolveJobId(null);
    setBatchJobId(null);
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
      setMlStatusMessage(ML_VERIFYING_MESSAGE);

      const connection = await readMlConnectionStatus();

      setMlConnected(connection.connected);
      setMlVerified(connection.verified);
      setMlUserId(connection.userId);
      setMlStatusMessage(connection.statusMessage);
    } catch (error) {
      setMlConnected(false);
      setMlVerified(false);
      setMlUserId(null);
      setMlStatusMessage("No se pudo verificar la conexión con Mercado Libre");
    } finally {
      setCheckingConnection(false);
    }
  };

  const isExcelFile = (f) => {
    if (!f) return false;
    const nameOk = f.name?.toLowerCase().endsWith(".xlsx");
    const typeOk =
      f.type ===
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
      f.type === "" ||
      f.type === "application/octet-stream";
    return nameOk && typeOk;
  };

  const isCsvFile = (f) => {
    if (!f) return false;
    const nameOk = f.name?.toLowerCase().endsWith(".csv");
    const typeOk =
      f.type === "text/csv" ||
      f.type === "application/vnd.ms-excel" ||
      f.type === "" ||
      f.type === "application/csv";
    return nameOk && typeOk;
  };

  const handleFileChange = (e) => {
    if (!mlVerified) return;

    const selectedFile = e.target.files?.[0];
    if (!selectedFile) return;

    if (!isExcelFile(selectedFile) && !isCsvFile(selectedFile)) {
      setFile(null);
      setResolveJobId(null);
      setBatchJobId(null);
      setStatus("error");
      setMessage("Archivo no válido. Selecciona un Excel (.xlsx) o CSV (.csv).");
      return;
    }

    setFile(selectedFile);
    setResolveJobId(null);
    setBatchJobId(null);
    setStatus("idle");
    setMessage("");
    setJobResult(null);
    setShowResultModal(false);
    setProgress(0);
    setProcessMessage("");
  };

  const startResolveProductsJob = async (fileToUpload) => {
    if (!mlUserId) {
      throw new Error("No se encontró user_id de Mercado Libre conectado.");
    }

    const formData = new FormData();
    formData.append("file", fileToUpload);

    const res = await fetch(
      `${API_BASE}/imports/resolve-products?user_id=${encodeURIComponent(mlUserId)}`,
      {
        method: "POST",
        body: formData,
        credentials: "include",
      }
    );

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data?.detail || data?.message || "No se pudo iniciar la resolución de product_id."
      );
    }

    if (!data?.job_id) {
      throw new Error("No se recibió job_id del proceso de resolución.");
    }

    return data.job_id;
  };

  const startBatchCompatibilitiesJob = async (resolvedJobIdValue) => {
    if (!mlUserId) {
      throw new Error("No se encontró user_id de Mercado Libre conectado.");
    }

    const res = await fetch(`${API_BASE}/imports/add-compatibilities-batch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({
        user_id: String(mlUserId),
        resolved_job_id: resolvedJobIdValue,
      }),
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data?.detail || data?.message || "No se pudo iniciar la carga batch."
      );
    }

    if (!data?.job_id) {
      throw new Error("No se recibió job_id del proceso batch.");
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

  const pollStageJob = async (
    currentJobId,
    {
      stageLabel,
      progressBase,
      progressSpan,
    }
  ) => {
    let finished = false;

    while (!finished) {
      try {
        const r = await fetch(`${API_BASE}/imports/${currentJobId}`, {
          credentials: "include",
        });

        const data = await r.json().catch(() => ({}));

        if (!r.ok) {
          throw new Error(
            data?.detail || data?.message || "Error consultando el estado del proceso."
          );
        }

        const currentProgress =
          typeof data.progress === "number" ? data.progress : 0;

        const mappedProgress = Math.min(
          100,
          progressBase + Math.round((currentProgress / 100) * progressSpan)
        );

        setProgress(mappedProgress);
        setProcessMessage(
          `${stageLabel}: ${data.message || "Procesando..."}`
        );
        setMessage(data.message || "");

        if (data.status === "success") {
          finished = true;
          return data;
        }

        if (data.status === "error") {
          throw new Error(data.message || `Error en etapa ${stageLabel}`);
        }

        await sleep(1200);
      } catch (err) {
        throw err;
      }
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
      setProcessMessage("Iniciando resolución de productos...");

      const newResolveJobId = await startResolveProductsJob(file);
      setResolveJobId(newResolveJobId);

      await pollStageJob(newResolveJobId, {
        stageLabel: "Etapa 1/2 - Resolviendo product_id",
        progressBase: 0,
        progressSpan: 50,
      });

      setProgress(50);
      setProcessMessage("Etapa 1 completada. Iniciando carga batch...");

      const newBatchJobId = await startBatchCompatibilitiesJob(newResolveJobId);
      setBatchJobId(newBatchJobId);

      await pollStageJob(newBatchJobId, {
        stageLabel: "Etapa 2/2 - Agregando compatibilidades batch",
        progressBase: 50,
        progressSpan: 50,
      });

      setProgress(100);
      setStatus("success");

      try {
        setLoadingResult(true);
        await fetchJobResult(newBatchJobId);
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

  const acceptText = "Archivo permitido: .xlsx o .csv";
  const buttonText =
    status === "processing" ? "Procesando..." : "Procesar Archivo";

  const connectButtonText = checkingConnection
    ? "Verificando conexión..."
    : mlVerified
    ? "✅ Cuenta conectada"
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
              📂 Elegir archivo (Excel o CSV)
            </label>

            <input
              ref={fileInputRef}
              id="fileInput"
              className="file-input"
              type="file"
              accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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

          {(resolveJobId || batchJobId) && (
            <div className="job-debug-info">
              {resolveJobId && <p>Job resolución: {resolveJobId}</p>}
              {batchJobId && <p>Job batch: {batchJobId}</p>}
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

      <PublicationsWithoutCompatibilityModal
        open={showPublicationsModal}
        onClose={handleClosePublicationsModal}
        apiBase={API_BASE}
      />
    </>
  );
}

export default CompatibilitiesUpload;
