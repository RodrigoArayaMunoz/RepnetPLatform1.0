import { useState } from "react";
import { LoaderCircle, Plug } from "lucide-react";
import "../styles/SupplierIntegrations.css";

const simulateRefaxConnection = () =>
  new Promise((resolve) => {
    window.setTimeout(() => resolve({ connected: true }), 1200);
  });

export default function SupplierIntegrations({
  connectRefax = simulateRefaxConnection,
}) {
  const [connectionStatus, setConnectionStatus] = useState("idle");
  const isConnecting = connectionStatus === "connecting";
  const isConnected = connectionStatus === "connected";

  const handleConnect = async () => {
    if (isConnecting || isConnected) return;

    setConnectionStatus("connecting");

    try {
      const result = await connectRefax();
      setConnectionStatus(result?.connected ? "connected" : "disconnected");
    } catch (error) {
      console.error("No se pudo establecer la conexión con REFAX:", error);
      setConnectionStatus("disconnected");
    }
  };

  const buttonLabel = isConnecting
    ? "Conectando..."
    : isConnected
      ? "Conectado"
      : connectionStatus === "disconnected"
        ? "No Conectado"
        : "Conectar";

  return (
    <section className="supplier-integrations-page">
      <div className="supplier-integrations-layout">
        <article className="supplier-integration-card">
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
            className={`supplier-integration-card__button supplier-integration-card__button--${connectionStatus}`}
            onClick={handleConnect}
            disabled={isConnecting || isConnected}
            aria-busy={isConnecting}
            aria-live="polite"
          >
            {isConnecting ? (
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
