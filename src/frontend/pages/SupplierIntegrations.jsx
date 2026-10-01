import { useEffect, useRef, useState } from "react";
import { Download, LoaderCircle, Plug } from "lucide-react";
import RefaxDownloadModal from "../components/RefaxDownloadModal.jsx";
import {
  connectRefax as requestRefaxConnection,
  downloadRefaxProducts as requestRefaxProducts,
  getRefaxStatus as requestRefaxStatus,
} from "../../lib/refaxConnection.js";
import "../styles/SupplierIntegrations.css";

export default function SupplierIntegrations({
  connectRefax = requestRefaxConnection,
  getRefaxStatus = requestRefaxStatus,
  onDownloadProducts = requestRefaxProducts,
}) {
  const [connectionStatus, setConnectionStatus] = useState("checking");
  const [connectionError, setConnectionError] = useState("");
  const [downloadStatus, setDownloadStatus] = useState("idle");
  const [downloadError, setDownloadError] = useState("");
  const [downloadProgress, setDownloadProgress] = useState({
    stage: "preparing",
    percentage: 0,
  });
  const downloadController = useRef(null);
  const isChecking = connectionStatus === "checking";
  const isConnecting = connectionStatus === "connecting";
  const isConnected = connectionStatus === "connected";
  const isBusy = isChecking || isConnecting;
  const isDownloading = downloadStatus === "downloading" || downloadStatus === "complete";

  useEffect(() => () => downloadController.current?.abort(), []);

  useEffect(() => {
    if (downloadStatus !== "complete") return undefined;
    const timer = window.setTimeout(() => setDownloadStatus("idle"), 700);
    return () => window.clearTimeout(timer);
  }, [downloadStatus]);

  useEffect(() => {
    let isMounted = true;

    getRefaxStatus()
      .then((result) => {
        if (!isMounted) return;
        setConnectionStatus(result?.connected ? "connected" : "idle");
        setConnectionError(result?.last_error || "");
      })
      .catch((error) => {
        if (!isMounted) return;
        setConnectionStatus("disconnected");
        setConnectionError(
          error?.message || "No se pudo verificar la conexión con REFAX."
        );
      });

    return () => {
      isMounted = false;
    };
  }, [getRefaxStatus]);

  const handleConnect = async () => {
    if (isBusy || isConnected) return;

    setConnectionStatus("connecting");
    setConnectionError("");

    try {
      const result = await connectRefax();
      if (!result?.connected) {
        throw new Error("REFAX no confirmó la conexión.");
      }
      setConnectionStatus("connected");
    } catch (error) {
      console.error("No se pudo establecer la conexión con REFAX:", error);
      setConnectionStatus("disconnected");
      setConnectionError(
        error?.message || "No se pudo establecer la conexión con REFAX."
      );
    }
  };

  const handleDownloadProducts = async () => {
    if (!isConnected || isDownloading || downloadController.current) return;

    const controller = new AbortController();
    downloadController.current = controller;
    setDownloadStatus("downloading");
    setDownloadError("");
    setDownloadProgress({ stage: "preparing", percentage: 0 });

    try {
      const { blob, filename } = await onDownloadProducts({
        signal: controller.signal,
        onProgress: (progress) => {
          if (!controller.signal.aborted) setDownloadProgress(progress);
        },
      });
      if (controller.signal.aborted) return;
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      setDownloadProgress({ stage: "downloading", percentage: 100 });
      setDownloadStatus("complete");
    } catch (error) {
      if (controller.signal.aborted) return;
      setDownloadStatus("error");
      setDownloadError(
        error?.message || "No se pudieron descargar los productos desde REFAX."
      );
    } finally {
      if (downloadController.current === controller) downloadController.current = null;
    }
  };

  const buttonLabel = isBusy
    ? isChecking
      ? "Verificando..."
      : "Conectando..."
    : isConnected
      ? "Conectado"
      : connectionStatus === "disconnected"
        ? "No Conectado"
        : "Conectar";

  return (
    <section className="supplier-integrations-page">
      {isDownloading && (
        <RefaxDownloadModal
          progress={downloadProgress}
          complete={downloadStatus === "complete"}
        />
      )}
      <div className="supplier-integrations-layout">
        <article className="supplier-integration-card">
          <div className="supplier-integration-card__content">
            <div className="supplier-integration-card__identity">
              <span className="supplier-integration-card__icon" aria-hidden="true">
                <Plug size={23} strokeWidth={2} />
              </span>

              <div>
                <h1>Integración REFAX</h1>
                <p>Conecta tu cuenta de proveedor con la plataforma.</p>
              </div>
            </div>

            <button
              type="button"
              className="supplier-integration-card__download-button"
              onClick={handleDownloadProducts}
              disabled={!isConnected || isDownloading}
              aria-busy={isDownloading}
              title={
                isConnected
                  ? "Descargar Excel con SKU, PRECIO y STOCK desde REFAX"
                  : "Conecta REFAX correctamente para descargar productos"
              }
            >
              {isDownloading ? (
                <LoaderCircle
                  className="supplier-integration-card__spinner"
                  size={17}
                  aria-hidden="true"
                />
              ) : (
                <Download size={17} aria-hidden="true" />
              )}
              {isDownloading ? "Preparando Excel..." : "Descargar Productos"}
            </button>

            {connectionError || downloadError ? (
              <p className="supplier-integration-card__error" role="alert">
                {connectionError || downloadError}
              </p>
            ) : null}
          </div>

          <button
            type="button"
            className={`supplier-integration-card__button supplier-integration-card__button--${connectionStatus}`}
            onClick={handleConnect}
            disabled={isBusy || isConnected}
            aria-busy={isBusy}
            aria-live="polite"
          >
            {isBusy ? (
              <LoaderCircle
                className="supplier-integration-card__spinner"
                size={17}
                aria-hidden="true"
              />
            ) : null}
            {buttonLabel}
          </button>
        </article>
      </div>
    </section>
  );
}
