import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ClipboardList,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { authFetch } from "../../lib/apiClient.js";
import "../styles/SellerSales.css";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const chileToday = () => {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Santiago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};
const REPNET_SALES_DATE_FROM = chileToday();
const REPNET_SALES_DATE_TO = REPNET_SALES_DATE_FROM;
const columns = ["VENTA / PACK", "ENVÍO", "PRODUCTOS", "ESTADO", "NOTA VENTA"];

const readErrorMessage = (data, fallback) => {
  if (typeof data?.detail === "string") return data.detail;
  if (typeof data?.detail?.message === "string") return data.detail.message;
  if (typeof data?.message === "string") return data.message;
  return fallback;
};

const formatSalesDate = (value) => {
  const [year, month, day] = String(value || "").split("-").map(Number);
  if (!year || !month || !day) return "hoy";

  return new Intl.DateTimeFormat("es-CL", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
};

function EmptyGridState({ children, icon }) {
  return (
    <tr>
      <td colSpan={columns.length} className="seller-sales-empty-cell">
        <div className="seller-sales-empty-state">{icon}{children}</div>
      </td>
    </tr>
  );
}

function SalesGrid({
  title,
  rows = [],
  isLoading = false,
  error = "",
  onRetry,
  isSyncing = false,
  disabled = false,
}) {
  return (
    <article className="seller-sales-grid-card">
      <header className="seller-sales-grid-header">
        <div>
          <h2>{title}</h2>
          {!disabled && !isLoading && !error ? (
            <span className="seller-sales-grid-count">
              {rows.length} {rows.length === 1 ? "venta" : "ventas"}
            </span>
          ) : null}
        </div>

        {onRetry ? (
          <button
            type="button"
            className="seller-sales-refresh-button"
            onClick={onRetry}
            disabled={isLoading || isSyncing}
            aria-label={`Sincronizar ventas de ${title}`}
            title="Sincronizar ventas con Mercado Libre"
          >
            <RefreshCw
              className={isSyncing ? "seller-sales-loading-icon" : ""}
              size={17}
              aria-hidden="true"
            />
          </button>
        ) : null}
      </header>

      <div className="seller-sales-grid-scroll">
        <table className="seller-sales-grid" aria-label={`Ventas ${title}`}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {isLoading ? (
              <EmptyGridState
                icon={
                  <LoaderCircle
                    className="seller-sales-loading-icon"
                    size={32}
                    aria-hidden="true"
                  />
                }
              >
                <strong>Cargando ventas del período...</strong>
                <span>Consultando órdenes y notas en Mercado Libre.</span>
              </EmptyGridState>
            ) : error ? (
              <EmptyGridState
                icon={
                  <span
                    className="seller-sales-empty-icon seller-sales-error-icon"
                    aria-hidden="true"
                  >
                    <AlertCircle size={27} strokeWidth={1.8} />
                  </span>
                }
              >
                <strong>No fue posible cargar las ventas</strong>
                <span>{error}</span>
                <button
                  type="button"
                  className="seller-sales-retry-button"
                  onClick={onRetry}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  Reintentar
                </button>
              </EmptyGridState>
            ) : disabled ? (
              <EmptyGridState
                icon={
                  <span className="seller-sales-empty-icon" aria-hidden="true">
                    <ClipboardList size={27} strokeWidth={1.8} />
                  </span>
                }
              >
                <strong>Integración pendiente</strong>
                <span>La cuenta EMILIA se conectará en una siguiente etapa.</span>
              </EmptyGridState>
            ) : rows.length === 0 ? (
              <EmptyGridState
                icon={
                  <span className="seller-sales-empty-icon" aria-hidden="true">
                    <ClipboardList size={27} strokeWidth={1.8} />
                  </span>
                }
              >
                <strong>No hay ventas pagadas en el período</strong>
                <span>Se muestran órdenes cerradas durante las fechas seleccionadas.</span>
              </EmptyGridState>
            ) : (
              rows.map((sale) => (
                <tr key={sale.sale_id || sale.order_id}>
                  <td
                    className="seller-sales-order-cell"
                    title={(sale.order_ids || [sale.order_id]).join(", ")}
                  >
                    {sale.sale_id || sale.mlc}
                    {sale.order_ids?.length > 1 ? (
                      <small>{sale.order_ids.length} órdenes</small>
                    ) : null}
                  </td>
                  <td>
                    <span
                      className={`seller-sales-shipping-badge seller-sales-shipping-badge--${sale.shipping_type || "pending"}`}
                    >
                      {sale.shipping_type === "flex"
                        ? "Flex"
                        : sale.shipping_type === "normal"
                          ? "Normal"
                          : sale.shipping_type === "no_shipping"
                            ? "Sin envío"
                            : "Pendiente"}
                    </span>
                    {sale.logistic_type ? <small>{sale.logistic_type}</small> : null}
                  </td>
                  <td className="seller-sales-products-cell">
                    {sale.items?.length ? (
                      sale.items.map((item) => (
                        <div key={`${item.order_id}-${item.line_number}`}>
                          <strong>{item.sku || item.item_id}</strong>
                          <span> × {item.quantity}</span>
                          {item.title ? <small>{item.title}</small> : null}
                        </div>
                      ))
                    ) : (
                      <span className="seller-sales-note-empty">Sin productos cargados</span>
                    )}
                  </td>
                  <td>
                    <strong>{sale.shipping_status || sale.status || "—"}</strong>
                  </td>
                  <td className="seller-sales-note-cell">
                    {sale.notes_error ? (
                      <span className="seller-sales-note-error">
                        No fue posible consultar la nota.
                      </span>
                    ) : sale.sale_note ? (
                      sale.sale_note
                    ) : (
                      <span className="seller-sales-note-empty">Sin nota de venta</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </article>
  );
}

export default function SellerSales() {
  const [repnetSales, setRepnetSales] = useState([]);
  const [salesPeriod, setSalesPeriod] = useState({ from: "", to: "" });
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  const [loadError, setLoadError] = useState("");

  const loadRepnetSales = useCallback(async (signal) => {
    setIsLoading(true);
    setLoadError("");

    try {
      const params = new URLSearchParams({
        date: REPNET_SALES_DATE_FROM,
        date_to: REPNET_SALES_DATE_TO,
      });
      const response = await authFetch(
        `${API_BASE}/ml/sales/today?${params.toString()}`,
        {
          method: "GET",
          credentials: "include",
          signal,
        }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          readErrorMessage(data, "No se pudieron consultar las ventas de REPNET.")
        );
      }

      setRepnetSales(Array.isArray(data?.sales) ? data.sales : []);
      setSalesPeriod({
        from: data?.date_from || REPNET_SALES_DATE_FROM,
        to: data?.date_to || REPNET_SALES_DATE_TO,
      });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setRepnetSales([]);
      setLoadError(
        error?.message || "No se pudieron consultar las ventas de REPNET."
      );
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  }, []);

  const syncRepnetSales = useCallback(async () => {
    setIsSyncing(true);
    setLoadError("");
    try {
      const params = new URLSearchParams({
        date: REPNET_SALES_DATE_FROM,
        date_to: REPNET_SALES_DATE_TO,
      });
      const response = await authFetch(
        `${API_BASE}/ml/sales/sync?${params.toString()}`,
        { method: "POST", credentials: "include" }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          readErrorMessage(data, "No se pudo iniciar la sincronización de ventas.")
        );
      }

      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const statusResponse = await authFetch(
          `${API_BASE}/ml/sales/sync/${data.task_id}`,
          { method: "GET", credentials: "include" }
        );
        const statusData = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok) {
          throw new Error(
            readErrorMessage(statusData, "No se pudo revisar la sincronización.")
          );
        }
        if (!statusData.ready) continue;
        if (!statusData.successful) {
          throw new Error(statusData.error || "La sincronización de ventas falló.");
        }
        await loadRepnetSales();
        return;
      }
      throw new Error("La sincronización continúa ejecutándose. Intenta nuevamente en unos minutos.");
    } catch (error) {
      setLoadError(error?.message || "No se pudieron sincronizar las ventas.");
    } finally {
      setIsSyncing(false);
    }
  }, [loadRepnetSales]);

  useEffect(() => {
    const controller = new AbortController();
    loadRepnetSales(controller.signal);
    return () => controller.abort();
  }, [loadRepnetSales]);

  return (
    <section className="seller-sales-page">
      <div className="seller-sales-layout">
        <header className="seller-sales-header">
          <h1>Gestión de Ventas</h1>
          <p>
            Ventas pagadas entre el {formatSalesDate(salesPeriod.from)} y el{" "}
            {formatSalesDate(salesPeriod.to)}, según la fecha de cierre
            registrada por Mercado Libre Chile.
          </p>
        </header>

        <div className="seller-sales-grids">
          <div className="seller-sales-grid-section">
            <SalesGrid
              title="REPNET"
              rows={repnetSales}
              isLoading={isLoading}
              error={loadError}
              onRetry={syncRepnetSales}
              isSyncing={isSyncing}
            />
          </div>

          <div className="seller-sales-grid-section">
            <span className="seller-sales-divider" aria-hidden="true" />
            <SalesGrid title="EMILIA" disabled />
          </div>
        </div>
      </div>
    </section>
  );
}
