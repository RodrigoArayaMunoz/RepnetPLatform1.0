import { useCallback, useEffect, useState } from "react";
import { supabase } from "../../lib/supabase.js";
import { authFetch } from "../../lib/apiClient.js";
import { fetchProcessPage, PROCESS_PAGE_SIZE } from "../utils/processPages.js";
import { createVisiblePoller, pollingError } from "../utils/visiblePolling.js";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const mapRow = (row, summary = {}) => {
  const date = row.fecha_proceso?.split("-").reverse().join("-") || "-";
  return {
    id: row.id, procesoId: row.proceso_id, archivo: row.archivo,
    fecha: `${date} ${(row.hora_proceso || "00:00:00").slice(0, 8)}`,
    procesadoPor: row.generado, estado: row.estado,
    displayEstado: summary.display_status || row.estado,
    hasErrorDetails: Boolean(summary.has_error_details) || String(row.estado).toLowerCase() === "error",
    isPartialProcess: Boolean(summary.is_partial),
    hasExportResult: Boolean(summary.has_export_result),
    exportKind: summary.export_kind || null,
    exportLabel: summary.export_label || "Descargar resultado",
  };
};

function useProcessLane(lane, revision, currentId, refresh, intervalMs) {
  const [page, setPage] = useState(1);
  const [value, setValue] = useState({ rows: [], total: 0, page: 1, loading: true, error: "" });
  useEffect(() => {
    if (revision === null) return undefined;
    let disposed = false;
    const stop = createVisiblePoller({
      intervalMs,
      run: async (signal) => {
        if (!supabase) throw Object.assign(new Error("Supabase no está configurado."), { status: 400 });
        const result = await fetchProcessPage(supabase, { lane, page, currentId, signal });
        if (signal.aborted || disposed) return;
        const lastPage = Math.max(1, Math.ceil(result.total / PROCESS_PAGE_SIZE));
        if (page > lastPage) {
          setPage(lastPage);
          return false;
        }
        let summaries = {};
        let summaryError = "";
        if (result.rows.length) {
          const response = await authFetch(`${API_BASE}/process-queue/errors/summaries`, {
            method: "POST", signal, credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ row_ids: result.rows.map((row) => String(row.id)) }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            // A revoked session must halt the whole polling cycle.
            if ([401, 402, 403, 404, 410].includes(response.status)) {
              throw pollingError(response, data.detail || "No se pudo consultar el historial.");
            }
            summaryError = "No se pudieron cargar los detalles de resultados. Actualiza la página para reintentar.";
          } else summaries = data.items || {};
        }
        if (!signal.aborted && !disposed) setValue({
          rows: result.rows.map((row) => mapRow(row, summaries[String(row.id)])),
          total: result.total, page, loading: false, error: summaryError,
        });
        if (summaryError) return false;
      },
      onError: (error) => {
        if (!disposed) setValue((old) => ({
          ...old, rows: old.page === page ? old.rows : [], page,
          loading: false, error: error.message,
        }));
      },
    });
    return () => { disposed = true; stop(); };
  }, [lane, revision, currentId, refresh, page, intervalMs]);
  return {
    ...value, rows: value.page === page ? value.rows : [],
    loading: value.loading || value.page !== page, page, setPage,
  };
}

export default function useProcessPages({ revision, currentId, running }) {
  const [refresh, setRefresh] = useState(0);
  const intervalMs = running ? 60_000 : 120_000;
  const processing = useProcessLane("processing", revision, currentId, refresh, intervalMs);
  const pending = useProcessLane("pending", revision, currentId, refresh, intervalMs);
  const completed = useProcessLane("completed", revision, currentId, refresh, intervalMs);
  const reload = useCallback(() => setRefresh((value) => value + 1), []);
  return { lanes: { processing, pending, completed }, reload };
}
