import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ClipboardList, LoaderCircle, RefreshCw } from "lucide-react";
import { supabase, supabaseConfigErrorMessage } from "../../lib/supabase.js";
import {
  formatChileClock,
  formatChileDateTime,
  getDeadlineStatus,
  isPendingReturn,
} from "../utils/returnsProjection.js";
import "../styles/Returns.css";

const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const PAGE_SIZE = 1000;

function LiveClock() {
  const [currentTime, setCurrentTime] = useState(() => new Date());

  useEffect(() => {
    let timeoutId;
    const tick = () => {
      setCurrentTime(new Date());
      timeoutId = window.setTimeout(tick, 1000 - (Date.now() % 1000));
    };
    const syncWhenVisible = () => {
      if (document.visibilityState === "visible") setCurrentTime(new Date());
    };

    timeoutId = window.setTimeout(tick, 1000 - (Date.now() % 1000));
    document.addEventListener("visibilitychange", syncWhenVisible);
    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("visibilitychange", syncWhenVisible);
    };
  }, []);

  return (
    <time className="returns-board__clock" dateTime={currentTime.toISOString()}>
      {formatChileClock(currentTime)}
    </time>
  );
}

async function fetchReturns() {
  const result = [];
  for (let offset = 0; ; offset += PAGE_SIZE) {
    // The optional fecha_limite_revision is read when the ML integration stores it.
    // Selecting * keeps the board compatible with the current database schema.
    const { data, error } = await supabase
      .from("devoluciones")
      .select("*")
      .order("id", { ascending: false })
      .range(offset, offset + PAGE_SIZE - 1);
    if (error) throw error;
    result.push(...(data || []));
    if (!data || data.length < PAGE_SIZE) return result;
  }
}

export default function Returns() {
  const [returns, setReturns] = useState([]);
  const [isLoading, setIsLoading] = useState(Boolean(supabase));
  const [loadError, setLoadError] = useState(
    supabase ? "" : supabaseConfigErrorMessage
  );
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const clockId = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(clockId);
  }, []);

  useEffect(() => {
    if (!supabase) return undefined;
    let cancelled = false;
    let fetching = false;

    const load = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const data = await fetchReturns();
        if (!cancelled) {
          setReturns(data);
          setLoadError("");
        }
      } catch (error) {
        console.error("No se pudieron cargar las devoluciones:", error);
        if (!cancelled) {
          setLoadError("No se pudieron actualizar las devoluciones desde Supabase.");
        }
      } finally {
        fetching = false;
        if (!cancelled) setIsLoading(false);
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") load();
    };

    load();
    const intervalId = window.setInterval(load, REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshVersion]);

  const pendingReturns = useMemo(() =>
    returns
      .filter(isPendingReturn)
      .sort((left, right) => {
        const leftTime = Date.parse(left.fecha_limite_revision || "");
        const rightTime = Date.parse(right.fecha_limite_revision || "");
        return (Number.isNaN(leftTime) ? Infinity : leftTime) -
          (Number.isNaN(rightTime) ? Infinity : rightTime);
      }), [returns]);

  const handleRetry = () => {
    setLoadError("");
    setIsLoading(true);
    setRefreshVersion((version) => version + 1);
  };

  return (
    <section className="returns-page">
      <div className="returns-board">
        <header className="returns-board__header">
          <div className="returns-board__heading">
            <h1>DEVOLUCIONES POR VENCER</h1>
            <p>Para el equipo de almacén. Muestra el plazo de revisión después de la recepción física del producto.</p>
          </div>
          <div className="returns-board__summary">
            <LiveClock />
            <div className="returns-board__pending" aria-live="polite">
              Pendientes: <strong>{isLoading ? "…" : pendingReturns.length}</strong>
            </div>
          </div>
        </header>

        <div className="returns-board__legend" aria-label="Leyenda de vencimientos">
          <span><i className="returns-board__dot returns-board__dot--today" />Vence hoy</span>
          <span><i className="returns-board__dot returns-board__dot--tomorrow" />Vence mañana</span>
          <span><i className="returns-board__dot returns-board__dot--later" />Con plazo</span>
        </div>

        <div className="returns-board__table-wrap">
          {loadError && pendingReturns.length > 0 && (
            <div className="returns-board__warning" role="alert">
              <AlertCircle size={19} aria-hidden="true" />
              {loadError} Se muestran los últimos datos disponibles.
              <button type="button" onClick={handleRetry}>Reintentar</button>
            </div>
          )}
          <table className="returns-board__table" aria-label="Devoluciones pendientes por vencer">
            <thead>
              <tr>
                <th scope="col">MLC</th>
                <th scope="col">Fecha límite de revisión</th>
                <th scope="col">Estado</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && pendingReturns.length === 0 ? (
                <tr><td colSpan={3} className="returns-board__empty-cell">
                  <div className="returns-board__empty"><LoaderCircle className="returns-board__spinner" size={38} aria-hidden="true" /><strong>Cargando devoluciones...</strong></div>
                </td></tr>
              ) : loadError && pendingReturns.length === 0 ? (
                <tr><td colSpan={3} className="returns-board__empty-cell">
                  <div className="returns-board__empty" role="alert"><AlertCircle size={42} aria-hidden="true" /><strong>No fue posible cargar los datos</strong><span>{loadError}</span><button type="button" onClick={handleRetry}>Reintentar</button></div>
                </td></tr>
              ) : pendingReturns.length === 0 ? (
                <tr><td colSpan={3} className="returns-board__empty-cell">
                  <div className="returns-board__empty"><ClipboardList size={44} aria-hidden="true" /><strong>{returns.length === 0 ? "No hay devoluciones registradas" : "No hay devoluciones pendientes"}</strong><span>{returns.length === 0 ? "Los registros aparecerán aquí cuando se sincronicen desde Mercado Libre." : "Todas las devoluciones registradas están finalizadas."}</span></div>
                </td></tr>
              ) : pendingReturns.map((item) => {
                const status = getDeadlineStatus(item.fecha_limite_revision, now);
                return (
                  <tr key={item.id}>
                    <td>{item.mlc || "—"}</td>
                    <td>{formatChileDateTime(item.fecha_limite_revision)}</td>
                    <td><span className={`returns-board__badge returns-board__badge--${status.key}`}>{status.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <footer className="returns-board__footer">
          <span>Horario de Chile · Actualización automática cada 5 min.</span>
          <button type="button" onClick={handleRetry} disabled={isLoading} aria-label="Actualizar devoluciones ahora">
            <RefreshCw size={17} aria-hidden="true" /> Actualizar ahora
          </button>
        </footer>
      </div>
    </section>
  );
}
