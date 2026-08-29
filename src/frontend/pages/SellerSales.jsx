import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import { authFetch } from "../../lib/apiClient.js";
import SaleSkuScanner from "../components/SaleSkuScanner.jsx";
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
const SALES_PER_PAGE = 8;

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
    <div className="seller-sales-empty-cell">
      <div className="seller-sales-empty-state">{icon}{children}</div>
    </div>
  );
}

const getSaleNumber = (sale) => sale.sale_id || sale.mlc || sale.order_id;

const getShippingLabel = (shippingType) => {
  if (shippingType === "flex") return "Flex";
  if (shippingType === "normal") return "Normal";
  if (shippingType === "no_shipping") return "Sin envío";
  return "Pendiente";
};

function PickingProducts({ sale }) {
  if (!sale.items?.length) {
    return (
      <div className="seller-sales-picking-empty">
        <ClipboardList size={30} aria-hidden="true" />
        <span>Esta venta no tiene productos cargados.</span>
      </div>
    );
  }

  return (
    <div className="seller-sales-picking-products">
      {sale.items.map((item) => (
        <article
          className="seller-sales-picking-product"
          key={`${item.order_id}-${item.line_number}`}
        >
          <div>
            <span>SKU</span>
            <strong>{item.sku || item.item_id}</strong>
          </div>
          <div>
            <span>Cantidad</span>
            <strong>{item.quantity}</strong>
          </div>
          {item.title ? <p>{item.title}</p> : null}
        </article>
      ))}
    </div>
  );
}

const salesFilterOptions = [
  { id: "all", label: "TOTAL PEDIDOS" },
  { id: "flex", label: "FLEX" },
  { id: "normal", label: "NORMAL" },
];

const dispatchFilterOptions = [
  { id: "dispatched", label: "DESPACHADOS" },
  { id: "pending", label: "POR DESPACHAR" },
];

const pickingFilterOptions = [
  { id: "in_preparation", label: "EN PREPARACIÓN" },
  { id: "packed", label: "EMBALADO" },
];

const getPickingStatusLabel = (status) => {
  if (status === "in_preparation") return "En preparación";
  if (status === "packed") return "Embalado";
  return "";
};

function SalesGrid({
  title,
  rows = [],
  isLoading = false,
  error = "",
  onRetry,
  onPickingStatusChange,
  isSyncing = false,
  disabled = false,
}) {
  const [activeFilter, setActiveFilter] = useState("all");
  const [activeDispatchFilter, setActiveDispatchFilter] = useState("all");
  const [activePickingFilter, setActivePickingFilter] = useState("all");
  const [currentPage, setCurrentPage] = useState(1);
  const [pickingSale, setPickingSale] = useState(null);
  const dispatchCounts = {
    dispatched: rows.filter((sale) => sale.is_dispatched === true).length,
    pending: rows.filter((sale) => sale.is_dispatched === false).length,
  };
  const dispatchFilteredRows =
    activeDispatchFilter === "all"
      ? rows
      : rows.filter((sale) =>
          activeDispatchFilter === "dispatched"
            ? sale.is_dispatched === true
            : sale.is_dispatched === false
        );
  const counts = {
    all: dispatchFilteredRows.length,
    flex: dispatchFilteredRows.filter((sale) => sale.shipping_type === "flex").length,
    normal: dispatchFilteredRows.filter((sale) => sale.shipping_type === "normal").length,
  };
  const shippingFilteredRows =
    activeFilter === "all"
      ? dispatchFilteredRows
      : dispatchFilteredRows.filter(
          (sale) => sale.shipping_type === activeFilter
        );
  const pickingCounts = {
    in_preparation: shippingFilteredRows.filter(
      (sale) => sale.picking_status === "in_preparation"
    ).length,
    packed: shippingFilteredRows.filter(
      (sale) => sale.picking_status === "packed"
    ).length,
  };
  const filteredRows =
    activePickingFilter === "all"
      ? shippingFilteredRows
      : shippingFilteredRows.filter(
          (sale) => sale.picking_status === activePickingFilter
        );
  const totalPages = Math.max(
    1,
    Math.ceil(filteredRows.length / SALES_PER_PAGE)
  );
  const visiblePage = Math.min(currentPage, totalPages);
  const firstRowIndex = (visiblePage - 1) * SALES_PER_PAGE;
  const paginatedRows = filteredRows.slice(
    firstRowIndex,
    firstRowIndex + SALES_PER_PAGE
  );
  const activeShippingLabel =
    activeFilter === "flex"
      ? " Flex"
      : activeFilter === "normal"
        ? " normales"
        : "";
  const emptySalesTitle =
    activePickingFilter === "in_preparation"
      ? `No hay pedidos${activeShippingLabel} en preparación`
      : activePickingFilter === "packed"
        ? `No hay pedidos${activeShippingLabel} embalados`
        : activeDispatchFilter === "dispatched"
      ? `No hay pedidos${activeShippingLabel} despachados`
      : activeDispatchFilter === "pending"
        ? `No hay pedidos${activeShippingLabel} por despachar`
        : activeFilter === "all"
          ? "No hay ventas pagadas en el período"
          : `No hay envíos${activeShippingLabel}`;
  const hasActiveFilters =
    activeDispatchFilter !== "all" ||
    activeFilter !== "all" ||
    activePickingFilter !== "all";

  const selectFilter = (filterId) => {
    setActiveFilter(filterId);
    setCurrentPage(1);
    setPickingSale(null);
  };

  const selectDispatchFilter = (filterId) => {
    setActiveDispatchFilter((currentFilter) =>
      currentFilter === filterId ? "all" : filterId
    );
    setCurrentPage(1);
    setPickingSale(null);
  };

  const selectPickingFilter = (filterId) => {
    setActivePickingFilter((currentFilter) =>
      currentFilter === filterId ? "all" : filterId
    );
    setCurrentPage(1);
    setPickingSale(null);
  };

  useEffect(() => {
    if (!pickingSale) return undefined;

    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setPickingSale(null);
    };

    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [pickingSale]);

  return (
    <article className="seller-sales-grid-card">
      <header className="seller-sales-grid-header">
        <div>
          <h2>{title}</h2>
          {!disabled && !isLoading && !error ? (
            <span className="seller-sales-grid-count">
              {filteredRows.length} {filteredRows.length === 1 ? "venta" : "ventas"}
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

      <div
        className="seller-sales-dispatch-filters"
        aria-label={`Filtros por estado de despacho ${title}`}
      >
        {dispatchFilterOptions.map((filter) => (
          <button
            key={filter.id}
            type="button"
            className={`seller-sales-filter seller-sales-dispatch-filter seller-sales-dispatch-filter--${filter.id}${activeDispatchFilter === filter.id ? " seller-sales-filter--active" : ""}`}
            onClick={() => selectDispatchFilter(filter.id)}
            aria-pressed={activeDispatchFilter === filter.id}
            disabled={disabled || isLoading}
            title={
              activeDispatchFilter === filter.id
                ? "Presiona nuevamente para mostrar todos"
                : undefined
            }
          >
            <span>{filter.label}</span>
            <strong>{dispatchCounts[filter.id]}</strong>
          </button>
        ))}
      </div>

      <div className="seller-sales-filters" aria-label={`Filtros de ventas ${title}`}>
        {salesFilterOptions.map((filter) => (
          <button
            key={filter.id}
            type="button"
            className={`seller-sales-filter${activeFilter === filter.id ? " seller-sales-filter--active" : ""}`}
            onClick={() => selectFilter(filter.id)}
            aria-pressed={activeFilter === filter.id}
            disabled={disabled || isLoading}
          >
            <span>{filter.label}</span>
            <strong>{counts[filter.id]}</strong>
          </button>
        ))}
      </div>

      <div
        className="seller-sales-picking-filters"
        aria-label={`Filtros por estado de picking ${title}`}
      >
        {pickingFilterOptions.map((filter) => (
          <button
            key={filter.id}
            type="button"
            className={`seller-sales-filter seller-sales-picking-filter seller-sales-picking-filter--${filter.id}${activePickingFilter === filter.id ? " seller-sales-filter--active" : ""}`}
            onClick={() => selectPickingFilter(filter.id)}
            aria-pressed={activePickingFilter === filter.id}
            disabled={disabled || isLoading}
            title={
              activePickingFilter === filter.id
                ? "Presiona nuevamente para mostrar todos"
                : undefined
            }
          >
            <span>{filter.label}</span>
            <strong>{pickingCounts[filter.id]}</strong>
          </button>
        ))}
      </div>

      <div className="seller-sales-cards" aria-label={`Ventas ${title}`}>
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
        ) : filteredRows.length === 0 ? (
          <EmptyGridState
            icon={
              <span className="seller-sales-empty-icon" aria-hidden="true">
                <ClipboardList size={27} strokeWidth={1.8} />
              </span>
            }
          >
            <strong>{emptySalesTitle}</strong>
            <span>
              {hasActiveFilters
                ? "Selecciona otra combinación de filtros para revisar los demás pedidos."
                : "Se muestran órdenes cerradas durante las fechas seleccionadas."}
            </span>
          </EmptyGridState>
        ) : (
          paginatedRows.map((sale) => (
            <article
              className="seller-sales-sale-card"
              key={getSaleNumber(sale)}
            >
              <header className="seller-sales-sale-card-header">
                <div className="seller-sales-sale-summary">
                  <div
                    className="seller-sales-sale-number"
                    title={(sale.order_ids || [sale.order_id]).join(", ")}
                  >
                    <span>Venta:</span>
                    <strong>{getSaleNumber(sale)}</strong>
                    {sale.order_ids?.length > 1 ? (
                      <small>{sale.order_ids.length} órdenes</small>
                    ) : null}
                  </div>
                  <span
                    className={`seller-sales-shipping-badge seller-sales-shipping-badge--${sale.shipping_type || "pending"}`}
                  >
                    {getShippingLabel(sale.shipping_type)}
                  </span>
                  {sale.picking_status ? (
                    <span
                      className={`seller-sales-picking-status-badge seller-sales-picking-status-badge--${sale.picking_status}`}
                    >
                      {getPickingStatusLabel(sale.picking_status)}
                    </span>
                  ) : null}
                </div>
                {sale.is_dispatched === false &&
                sale.picking_status !== "packed" ? (
                  <button
                    type="button"
                    className="seller-sales-picking-button"
                    onClick={() => setPickingSale(sale)}
                    aria-haspopup="dialog"
                  >
                    {sale.picking_status === "in_preparation"
                      ? "Continuar Picking"
                      : "Iniciar Picking"}
                  </button>
                ) : sale.is_dispatched === true ||
                  sale.picking_status === "packed" ? (
                  <button
                    type="button"
                    className="seller-sales-picking-button seller-sales-detail-button"
                    onClick={() => setPickingSale(sale)}
                    aria-haspopup="dialog"
                  >
                    Ver Detalle
                  </button>
                ) : null}
              </header>

              <div className="seller-sales-sale-note">
                <span>Nota de venta</span>
                {sale.notes_error ? (
                  <strong className="seller-sales-note-error">
                    No fue posible consultar la nota.
                  </strong>
                ) : sale.sale_note ? (
                  <p>{sale.sale_note}</p>
                ) : (
                  <p className="seller-sales-note-empty">Sin nota de venta</p>
                )}
              </div>
            </article>
          ))
        )}
      </div>

      {!disabled && !isLoading && !error && filteredRows.length > SALES_PER_PAGE ? (
        <nav
          className="seller-sales-pagination"
          aria-label={`Paginación de ventas ${title}`}
        >
          <span className="seller-sales-pagination-summary">
            Mostrando {firstRowIndex + 1}-{Math.min(firstRowIndex + SALES_PER_PAGE, filteredRows.length)} de{" "}
            {filteredRows.length}
          </span>
          <div className="seller-sales-pagination-controls">
            <button
              type="button"
              onClick={() => setCurrentPage(Math.max(1, visiblePage - 1))}
              disabled={visiblePage === 1}
              aria-label="Página anterior"
            >
              <ChevronLeft size={17} aria-hidden="true" />
            </button>
            <span>
              Página {visiblePage} de {totalPages}
            </span>
            <button
              type="button"
              onClick={() =>
                setCurrentPage(Math.min(totalPages, visiblePage + 1))
              }
              disabled={visiblePage === totalPages}
              aria-label="Página siguiente"
            >
              <ChevronRight size={17} aria-hidden="true" />
            </button>
          </div>
        </nav>
      ) : null}

      {pickingSale
        ? createPortal(
            <div
              className="seller-sales-picking-overlay"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) setPickingSale(null);
              }}
            >
              <section
                className="seller-sales-picking-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="seller-sales-picking-title"
              >
                <header className="seller-sales-picking-modal-header">
                  <div>
                    <span className="seller-sales-picking-modal-eyebrow">
                      Venta
                    </span>
                    <h3 id="seller-sales-picking-title">
                      {getSaleNumber(pickingSale)}
                    </h3>
                    <span
                      className={`seller-sales-shipping-badge seller-sales-picking-modal-sale-type seller-sales-shipping-badge--${pickingSale.shipping_type || "pending"}`}
                    >
                      {getShippingLabel(pickingSale.shipping_type)}
                    </span>
                  </div>
                  <div className="seller-sales-picking-modal-actions">
                    <button
                      type="button"
                      className="seller-sales-picking-close-icon"
                      onClick={() => setPickingSale(null)}
                      aria-label="Cerrar detalle de picking"
                      autoFocus
                    >
                      <X size={20} aria-hidden="true" />
                    </button>
                  </div>
                </header>

                <div className="seller-sales-picking-modal-body">
                  {pickingSale.is_dispatched === false &&
                  pickingSale.picking_status !== "packed" ? (
                    <SaleSkuScanner
                      key={getSaleNumber(pickingSale)}
                      items={pickingSale.items}
                      saleNumber={getSaleNumber(pickingSale)}
                      onValidSku={({ code }) =>
                        onPickingStatusChange(
                          pickingSale,
                          pickingSale.picking_status === "in_preparation"
                            ? "packed"
                            : "in_preparation",
                          code
                        )
                      }
                      onValidationSuccess={() => setPickingSale(null)}
                    />
                  ) : null}

                  <div className="seller-sales-picking-modal-section-title">
                    <h4>
                      {pickingSale.is_dispatched ||
                      pickingSale.picking_status === "packed"
                        ? "Productos de la venta"
                        : "Productos para picking"}
                    </h4>
                    <span>
                      {pickingSale.items?.length || 0}{" "}
                      {pickingSale.items?.length === 1 ? "producto" : "productos"}
                    </span>
                  </div>
                  <PickingProducts sale={pickingSale} />
                </div>

                <footer className="seller-sales-picking-modal-footer">
                  <button type="button" onClick={() => setPickingSale(null)}>
                    Cerrar
                  </button>
                </footer>
              </section>
            </div>,
            document.body
          )
        : null}
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

  const updatePickingStatus = useCallback(
    async (sale, pickingStatus, scannedSku = null) => {
      const saleId = getSaleNumber(sale);
      const response = await authFetch(
        `${API_BASE}/ml/sales/${encodeURIComponent(saleId)}/picking-status`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status: pickingStatus,
            scanned_sku: scannedSku,
          }),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          readErrorMessage(data, "No fue posible guardar el estado de picking.")
        );
      }

      setRepnetSales((currentSales) =>
        currentSales.map((currentSale) =>
          getSaleNumber(currentSale) === saleId
            ? {
                ...currentSale,
                picking_status: data.status,
                picking_started_at: data.started_at,
                packed_at: data.packed_at,
                last_scanned_sku: data.last_scanned_sku,
              }
            : currentSale
        )
      );
      return data;
    },
    []
  );

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
          <h1>Gestión de Pedidos</h1>
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
              onPickingStatusChange={updatePickingStatus}
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
