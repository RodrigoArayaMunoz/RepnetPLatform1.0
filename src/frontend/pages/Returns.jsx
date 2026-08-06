import { useEffect, useState } from "react";
import {
  AlertCircle,
  ClipboardList,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import {
  supabase,
  supabaseConfigErrorMessage,
} from "../../lib/supabase.js";
import "../styles/Returns.css";

const columns = [
  { key: "mlc", label: "MLC" },
  { key: "sku", label: "SKU" },
  { key: "notas", label: "NOTAS" },
  { key: "fecha_ingreso", label: "FECHA DE INGRESO", type: "date" },
  { key: "fecha_revision", label: "FECHA DE REVISIÓN", type: "date" },
  { key: "estado_producto", label: "ESTADO DE PRODUCTO" },
  { key: "estado_nc", label: "ESTADO DE NC" },
  { key: "folio_nc", label: "FOLIO NC" },
  { key: "estado", label: "ESTADO" },
  { key: "mediacion", label: "MEDIACIÓN" },
];

const selectedFields = ["id", ...columns.map(({ key }) => key)].join(",");

const formatDate = (value) => {
  if (!value) return "—";

  const [year, month, day] = value.split("-");
  return year && month && day ? `${day}-${month}-${year}` : value;
};

const displayValue = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
};

export default function Returns() {
  const [returns, setReturns] = useState([]);
  const [isLoading, setIsLoading] = useState(Boolean(supabase));
  const [loadError, setLoadError] = useState(
    supabase ? "" : supabaseConfigErrorMessage
  );
  const [reloadVersion, setReloadVersion] = useState(0);

  useEffect(() => {
    if (!supabase) return undefined;

    let cancelled = false;

    supabase
      .from("devoluciones")
      .select(selectedFields)
      .order("id", { ascending: false })
      .then(({ data, error }) => {
        if (cancelled) return;

        if (error) {
          console.error("No se pudieron cargar las devoluciones:", error);
          setReturns([]);
          setLoadError(
            "No se pudieron cargar las devoluciones desde Supabase."
          );
        } else {
          setReturns(data || []);
          setLoadError("");
        }

        setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [reloadVersion]);

  const handleRetry = () => {
    setIsLoading(true);
    setLoadError("");
    setReloadVersion((currentVersion) => currentVersion + 1);
  };

  const recordLabel = isLoading
    ? "Cargando..."
    : `${returns.length} ${returns.length === 1 ? "registro" : "registros"}`;

  return (
    <section className="returns-page">
      <div className="returns-layout">
        <header className="returns-header">
          <div>
            <h1>Devoluciones</h1>
            <p>Consulta y realiza el seguimiento de las devoluciones.</p>
          </div>

          <span className="returns-counter">{recordLabel}</span>
        </header>

        <div className="returns-grid-card">
          <div className="returns-grid-scroll">
            <table className="returns-grid" aria-label="Listado de devoluciones">
              <thead>
                <tr>
                  {columns.map(({ key, label }) => (
                    <th key={key} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={columns.length} className="returns-empty-cell">
                      <div className="returns-empty-state" aria-live="polite">
                        <LoaderCircle
                          className="returns-loading-icon"
                          size={32}
                          aria-hidden="true"
                        />
                        <strong>Cargando devoluciones...</strong>
                      </div>
                    </td>
                  </tr>
                ) : loadError ? (
                  <tr>
                    <td colSpan={columns.length} className="returns-empty-cell">
                      <div
                        className="returns-empty-state returns-error-state"
                        role="alert"
                      >
                        <span className="returns-empty-icon" aria-hidden="true">
                          <AlertCircle size={28} strokeWidth={1.8} />
                        </span>
                        <strong>No fue posible cargar los datos</strong>
                        <span>{loadError}</span>
                        {supabase ? (
                          <button
                            type="button"
                            className="returns-retry-button"
                            onClick={handleRetry}
                          >
                            <RefreshCw size={16} aria-hidden="true" />
                            Reintentar
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ) : returns.length === 0 ? (
                  <tr>
                    <td colSpan={columns.length} className="returns-empty-cell">
                      <div className="returns-empty-state">
                        <span className="returns-empty-icon" aria-hidden="true">
                          <ClipboardList size={28} strokeWidth={1.8} />
                        </span>
                        <strong>No hay devoluciones para mostrar</strong>
                        <span>
                          Los registros aparecerán aquí cuando estén disponibles.
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  returns.map((returnItem) => (
                    <tr key={returnItem.id}>
                      {columns.map(({ key, type }) => (
                        <td key={key} title={displayValue(returnItem[key])}>
                          {type === "date"
                            ? formatDate(returnItem[key])
                            : displayValue(returnItem[key])}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
