import { useEffect, useRef, useState } from "react";
import {
  Camera,
  CheckCircle2,
  Keyboard,
  ScanLine,
  Square,
  XCircle,
} from "lucide-react";
import {
  findSaleItemBySku,
  normalizeSkuCode,
} from "../utils/skuValidation.js";

const cameraErrorMessage = (error) => {
  if (!window.isSecureContext) {
    return "La cámara solo funciona mediante HTTPS o en localhost.";
  }
  if (error?.name === "NotAllowedError") {
    return "Debes permitir el acceso a la cámara para escanear productos.";
  }
  if (error?.name === "NotFoundError") {
    return "No se encontró una cámara disponible en este dispositivo.";
  }
  if (error?.name === "NotReadableError") {
    return "La cámara está siendo utilizada por otra aplicación.";
  }
  return "No fue posible iniciar la cámara. Puedes ingresar el SKU manualmente.";
};

export default function SaleSkuScanner({ items = [], saleNumber }) {
  const videoRef = useRef(null);
  const controlsRef = useRef(null);
  const handledResultRef = useRef(false);
  const [isScanning, setIsScanning] = useState(false);
  const [manualCode, setManualCode] = useState("");
  const [scannerError, setScannerError] = useState("");
  const [validationResult, setValidationResult] = useState(null);

  const stopScanner = () => {
    controlsRef.current?.stop();
    controlsRef.current = null;
    setIsScanning(false);
  };

  useEffect(
    () => () => {
      controlsRef.current?.stop();
      controlsRef.current = null;
    },
    []
  );

  const validateCode = (rawCode) => {
    const code = normalizeSkuCode(rawCode);
    if (!code) return;

    const matchingItem = findSaleItemBySku(items, code);
    const nextResult = matchingItem
      ? {
          type: "success",
          code,
          item: matchingItem,
          message: `El SKU pertenece a la venta ${saleNumber}.`,
        }
      : {
          type: "error",
          code,
          item: null,
          message: `El SKU no pertenece a la venta ${saleNumber}.`,
        };

    setValidationResult(nextResult);
    setManualCode(code);

    if (navigator.vibrate) {
      navigator.vibrate(matchingItem ? 120 : [180, 90, 180]);
    }
  };

  const startScanner = async () => {
    setScannerError("");
    setValidationResult(null);
    handledResultRef.current = false;

    if (!navigator.mediaDevices?.getUserMedia) {
      setScannerError(
        "Este navegador no permite acceder a la cámara. Ingresa el SKU manualmente."
      );
      return;
    }

    setIsScanning(true);

    try {
      const { BrowserMultiFormatReader } = await import("@zxing/browser");
      const reader = new BrowserMultiFormatReader();
      const controls = await reader.decodeFromConstraints(
        {
          audio: false,
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
        },
        videoRef.current,
        (result, _error, scannerControls) => {
          if (!result || handledResultRef.current) return;

          handledResultRef.current = true;
          scannerControls.stop();
          controlsRef.current = null;
          setIsScanning(false);
          validateCode(result.getText());
        }
      );
      controlsRef.current = controls;
    } catch (error) {
      controlsRef.current?.stop();
      controlsRef.current = null;
      setIsScanning(false);
      setScannerError(cameraErrorMessage(error));
    }
  };

  const submitManualCode = (event) => {
    event.preventDefault();
    stopScanner();
    setScannerError("");
    validateCode(manualCode);
  };

  return (
    <section className="seller-sales-sku-scanner" aria-labelledby="sku-scanner-title">
      <div className="seller-sales-sku-scanner-heading">
        <div>
          <span>Validación de producto</span>
          <h4 id="sku-scanner-title">Escanear SKU</h4>
        </div>
        <ScanLine size={22} aria-hidden="true" />
      </div>

      <div
        className={`seller-sales-scanner-viewport${isScanning ? " seller-sales-scanner-viewport--active" : ""}`}
      >
        <video ref={videoRef} muted playsInline aria-label="Vista de la cámara" />
        {isScanning ? (
          <div className="seller-sales-scanner-guide" aria-hidden="true">
            <span />
          </div>
        ) : (
          <div className="seller-sales-scanner-placeholder">
            <Camera size={30} aria-hidden="true" />
            <span>Abre la cámara y apunta al código de barras o QR.</span>
          </div>
        )}
      </div>

      <button
        type="button"
        className={`seller-sales-scanner-camera-button${isScanning ? " seller-sales-scanner-camera-button--stop" : ""}`}
        onClick={isScanning ? stopScanner : startScanner}
      >
        {isScanning ? (
          <>
            <Square size={16} aria-hidden="true" />
            Detener cámara
          </>
        ) : (
          <>
            <Camera size={17} aria-hidden="true" />
            {validationResult ? "Escanear otro código" : "Abrir cámara"}
          </>
        )}
      </button>

      {scannerError ? (
        <div className="seller-sales-scan-feedback seller-sales-scan-feedback--error" role="alert">
          <XCircle size={20} aria-hidden="true" />
          <span>{scannerError}</span>
        </div>
      ) : null}

      {validationResult ? (
        <div
          className={`seller-sales-scan-feedback seller-sales-scan-feedback--${validationResult.type}`}
          role="status"
        >
          {validationResult.type === "success" ? (
            <CheckCircle2 size={22} aria-hidden="true" />
          ) : (
            <XCircle size={22} aria-hidden="true" />
          )}
          <div>
            <strong>{validationResult.code}</strong>
            <span>{validationResult.message}</span>
            {validationResult.item?.title ? (
              <small>{validationResult.item.title}</small>
            ) : null}
          </div>
        </div>
      ) : null}

      <form className="seller-sales-manual-sku" onSubmit={submitManualCode}>
        <label htmlFor={`manual-sku-${saleNumber}`}>
          <Keyboard size={16} aria-hidden="true" />
          Ingreso manual
        </label>
        <div>
          <input
            id={`manual-sku-${saleNumber}`}
            type="text"
            value={manualCode}
            onChange={(event) => setManualCode(event.target.value)}
            placeholder="Escribe o pega el SKU"
            autoComplete="off"
            autoCapitalize="characters"
          />
          <button type="submit" disabled={!manualCode.trim()}>
            Validar
          </button>
        </div>
      </form>
    </section>
  );
}
