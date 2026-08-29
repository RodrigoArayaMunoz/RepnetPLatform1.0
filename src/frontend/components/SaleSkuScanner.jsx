import { useEffect, useRef, useState } from "react";
import {
  Camera,
  Keyboard,
  ScanLine,
  XCircle,
} from "lucide-react";
import {
  expandValidatedSkuCounts,
  findSaleItemBySku,
  getSaleSkuRequirements,
  isSaleSkuValidationComplete,
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

export default function SaleSkuScanner({
  items = [],
  saleNumber,
  validatedSkuCounts = {},
  onProgressChange,
  onAllItemsValidated,
  onValidationSuccess,
}) {
  const videoRef = useRef(null);
  const controlsRef = useRef(null);
  const handledResultRef = useRef(false);
  const validationFinishedRef = useRef(true);
  const emptyFrameCountRef = useRef(0);
  const validateCodeRef = useRef(null);
  const [isScanning, setIsScanning] = useState(false);
  const [manualCode, setManualCode] = useState("");
  const [scannerError, setScannerError] = useState("");
  const [validationResult, setValidationResult] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const skuRequirements = getSaleSkuRequirements(items);

  const validateCode = async (rawCode) => {
    const code = normalizeSkuCode(rawCode);
    if (!code) return;

    const matchingItem = findSaleItemBySku(items, code);
    setManualCode(code);

    if (!matchingItem) {
      setValidationResult({
        type: "error",
        code,
        item: null,
        message: `El SKU no pertenece a la venta ${saleNumber}.`,
      });
      if (navigator.vibrate) navigator.vibrate([180, 90, 180]);
      return;
    }

    const validatedQuantity = Number(validatedSkuCounts[code] || 0);
    const requiredQuantity = Number(skuRequirements[code] || 0);
    if (validatedQuantity >= requiredQuantity) {
      setValidationResult({
        type: "error",
        code,
        item: matchingItem,
        message: "La cantidad requerida de este SKU ya está completa.",
      });
      if (navigator.vibrate) navigator.vibrate([180, 90, 180]);
      return;
    }

    const nextValidatedSkuCounts = {
      ...validatedSkuCounts,
      [code]: validatedQuantity + 1,
    };

    setValidationResult(null);
    setScannerError("");
    setManualCode("");
    onProgressChange?.(nextValidatedSkuCounts);

    if (!isSaleSkuValidationComplete(items, nextValidatedSkuCounts)) {
      if (navigator.vibrate) navigator.vibrate(80);
      return;
    }

    setIsSaving(true);
    try {
      await onAllItemsValidated?.({
        code,
        item: matchingItem,
        scannedSkus: expandValidatedSkuCounts(nextValidatedSkuCounts),
      });
      if (navigator.vibrate) navigator.vibrate(120);
      setIsSaving(false);
      onValidationSuccess?.();
    } catch (error) {
      setIsSaving(false);
      onProgressChange?.(validatedSkuCounts);
      setManualCode(code);
      setValidationResult({
        type: "error",
        code,
        item: matchingItem,
        message:
          error?.message || "No fue posible guardar el estado de picking.",
      });
    }
  };

  validateCodeRef.current = validateCode;

  useEffect(() => {
    let cancelled = false;

    const startScanner = async () => {
      setScannerError("");
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
          (result) => {
            if (cancelled) return;

            if (!result) {
              if (
                handledResultRef.current &&
                validationFinishedRef.current
              ) {
                emptyFrameCountRef.current += 1;
                if (emptyFrameCountRef.current >= 8) {
                  handledResultRef.current = false;
                  emptyFrameCountRef.current = 0;
                }
              }
              return;
            }

            emptyFrameCountRef.current = 0;
            if (handledResultRef.current) return;

            handledResultRef.current = true;
            validationFinishedRef.current = false;
            void Promise.resolve(
              validateCodeRef.current?.(result.getText())
            ).finally(() => {
              validationFinishedRef.current = true;
            });
          }
        );

        if (cancelled) {
          controls.stop();
          return;
        }
        controlsRef.current = controls;
      } catch (error) {
        controlsRef.current?.stop();
        controlsRef.current = null;
        if (cancelled) return;
        setIsScanning(false);
        setScannerError(cameraErrorMessage(error));
      }
    };

    void startScanner();

    return () => {
      cancelled = true;
      controlsRef.current?.stop();
      controlsRef.current = null;
    };
  }, []);

  const submitManualCode = (event) => {
    event.preventDefault();
    setScannerError("");
    handledResultRef.current = true;
    validationFinishedRef.current = false;
    emptyFrameCountRef.current = 0;
    void validateCode(manualCode).finally(() => {
      validationFinishedRef.current = true;
    });
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
            <span>
              {scannerError
                ? "La cámara no está disponible. Usa el ingreso manual."
                : "Iniciando cámara..."}
            </span>
          </div>
        )}
      </div>

      {scannerError ? (
        <div className="seller-sales-scan-feedback seller-sales-scan-feedback--error" role="alert">
          <XCircle size={20} aria-hidden="true" />
          <span>{scannerError}</span>
        </div>
      ) : null}

      {validationResult ? (
        <div
          className="seller-sales-scan-feedback seller-sales-scan-feedback--error"
          role="alert"
        >
          <XCircle size={22} aria-hidden="true" />
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
            disabled={isSaving}
          />
          <button type="submit" disabled={!manualCode.trim() || isSaving}>
            Validar
          </button>
        </div>
      </form>
    </section>
  );
}
