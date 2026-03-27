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
  const [checkingConnection, setCheckingConnection] = useState(true);
  const [mlStatusMessage, setMlStatusMessage] = useState(
    "Verificando conexión con Mercado Libre..."
  );

  const [jobResult, setJobResult] = useState(null);
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
      setMlStatusMessage("Verificando conexión con Mercado Libre...");

      const res = await fetch(`${API_BASE}/ml/status`, {
        method: "GET",
        credentials: "include",
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data?.connected === true) {
        setMlConnected(true);
        setMlVerified(true);
        setMlStatusMessage("Conectado exitosamente");
      } else {
        setMlConnected(false);
        setMlVerified(false);
        setMlStatusMessage("Debes conectar tu cuenta de Mercado Libre");
      }
    } catch (error) {
      setMlConnected(false);
      setMlVerified(false);
      setMlStatusMessage("No se pudo verificar la conexión con Mercado Libre");
    } finally {
      setCheckingConnection(false);
    }
  };

  const isExcelFile = (f) => {
    if (!f) return false;

    const name = f.name?.toLowerCase() || "";
    const validExtension = name.endsWith(".xlsx") || name.endsWith(".xls");

    const validMime =
      f.type ===
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
      f.type === "application/vnd.ms-excel" ||
      f.type === "" ||
      f.type === "application/octet-stream";

    return validExtension && validMime;
  };

  const handleFileChange = (e) => {
    if (!mlVerified) return;

    const selectedFile = e.target.files?.[0];
    if (!selectedFile) return;

    if (!isExcelFile(selectedFile)) {
      setFile(null);
      setStatus("error");
      setMessage("Archivo no válido. Selecciona un Excel (.xlsx o .xls).");
      return;
    }

    setFile(selectedFile);
    setStatus("idle");
    setMessage("");
    setJobResult(null);
    setShowResultModal(false);
    setProgress(0);
    setProcessMessage("");
  };

  const processNoCompatibilitiesFile = async (fileToUpload) => {
    const formData = new FormData();
    formData.append("file", fileToUpload);

    const res = await fetch(`${API_BASE}/compatibility-exceptions/upload`, {
      method: "POST",
      body: formData,
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail =
        data?.detail ||
        data?.message ||
        "Error procesando el archivo de no compatibilidades.";
      throw new Error(
        typeof detail === "string"
          ? detail
          : "Error procesando el archivo de no compatibilidades."
      );
    }

    return data;
  };

  const buildResultModalData = (apiResponse) => {
    return {
      summary: {
        total: apiResponse?.total ?? 0,
        success: apiResponse?.success ?? 0,
        errors: apiResponse?.errors ?? 0,
        comment_used: apiResponse?.comment_used ?? "",
        filename: apiResponse?.filename ?? file?.name ?? "",
      },
      results: apiResponse?.results ?? [],
    };
  };

  const handleProcess = async () => {
    if (!mlVerified) {
      setStatus("error");
      setMessage("Primero debes conectar tu cuenta de Mercado Libre.");
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
      setProgress(25);
      setProcessMessage("Subiendo archivo...");
      setMessage("");

      const response = await processNoCompatibilitiesFile(file);

      setProgress(85);
      setProcessMessage("Procesando resultado...");

      const modalData = buildResultModalData(response);
      setJobResult(modalData);

      setProgress(100);
      setStatus("success");
      setMessage(
        `Proceso finalizado. Éxitos: ${response?.success ?? 0}, errores: ${
          response?.errors ?? 0
        }.`
      );
      setShowResultModal(true);
    } catch (error) {
      setStatus("error");
      setMessage(
        error?.message || "Ocurrió un error al procesar el archivo."
      );
    } finally {
      setLoadingProcess(false);
      setProcessMessage("");
      setProgress(0);
    }
  };

  const handleConnectMercadoLibre = () => {
    if (checkingConnection || mlVerified) return;
    window.location.href = `${API_BASE}/auth/login`;
  };

  const acceptText = "Archivo permitido: .xlsx o .xls";
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
              📂 Elegir archivo (Excel)
            </label>

            <input
              ref={fileInputRef}
              id="fileInput"
              className="file-input"
              type="file"
              accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
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
                !file ||
                status === "processing" ||
                checkingConnection ||
                loadingProcess
              }
              type="button"
            >
              {loadingProcess ? "Procesando..." : buttonText}
            </button>

            <button
              className="process-button secondary-action-button"
              onClick={handleViewPublicationsWithoutCompatibilities}
              disabled={!mlVerified || checkingConnection || loadingProcess}
              type="button"
            >
              Ver Publicaciones No Informadas
            </button>
          </div>

          {message && !loadingProcess && (
            <p className={`status-message ${status}`}>{message}</p>
          )}
        </div>
      </section>



      <ResultViewNoCompatibilities
        open={showPublicationsModal}
        onClose={handleClosePublicationsModal}
        apiBase={API_BASE}
      />
    </>
  );
}

export default NoCompatibilitiesUpload;