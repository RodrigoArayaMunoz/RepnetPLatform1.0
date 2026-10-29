export const PROCESS_PAGE_SIZE = 3;
export const PROCESS_COLUMNS = "id,proceso_id,archivo,fecha_proceso,hora_proceso,generado,estado";

export function processQueueRevision(state) {
  // Per-row progress must not invalidate the database history.
  return JSON.stringify([
    Boolean(state.running), state.task_id, state.current_process_row_id,
    state.processed_count, state.completed_at,
  ]);
}

function check(result) {
  if (result.error) {
    throw Object.assign(new Error(result.error.message), { status: result.status });
  }
  return { rows: result.data || [], total: result.count ?? result.data?.length ?? 0 };
}

export async function fetchProcessPage(client, { lane, page = 1, currentId, signal }) {
  const query = () => client.from("procesos").select(PROCESS_COLUMNS, { count: "exact" });
  const ordered = (request) => {
    const pending = lane === "pending";
    const dated = request
      .order("fecha_proceso", { ascending: pending })
      .order("hora_proceso", { ascending: pending });
    // Match the worker's FIFO order, including files saved in the same second.
    if (pending) dated.order("proceso_id", { ascending: true });
    return dated.order("id", { ascending: pending });
  };
  const start = (Math.max(1, page) - 1) * PROCESS_PAGE_SIZE;
  let request = query();
  let current = [];
  if (lane === "processing") {
    if (currentId) {
      const result = check(await query().eq("id", currentId).limit(1).abortSignal(signal));
      current = result.rows;
      request = request.neq("id", currentId);
    }
    request = request.ilike("estado", "Procesando");
  } else if (lane === "pending") {
    request = request.ilike("estado", "Pendiente");
    if (currentId) request = request.neq("id", currentId);
  } else if (lane === "completed") {
    request = request.or("estado.is.null,and(estado.not.ilike.Pendiente,estado.not.ilike.Procesando)");
    if (currentId) request = request.neq("id", currentId);
  } else {
    throw new Error("Estado de procesos no reconocido.");
  }
  const includeCurrent = page === 1 && current.length > 0;
  const offset = Math.max(0, start - current.length);
  const limit = PROCESS_PAGE_SIZE - (includeCurrent ? current.length : 0);
  const response = await ordered(request).range(offset, offset + limit - 1).abortSignal(signal);
  if (response.status === 416) {
    // Rows may have moved or been deleted while a later page was open. Fetch
    // only the first row/count so the caller can clamp to the new last page.
    const first = check(await request.range(0, 0).abortSignal(signal));
    return { rows: [], total: first.total + current.length };
  }
  const result = check(response);
  return {
    rows: [...(includeCurrent ? current : []), ...result.rows],
    total: result.total + current.length,
  };
}
