export const PROCESS_STATUS = {
  PENDING: "Pendiente",
  PROCESSING: "Procesando",
  PROCESSED: "Procesado",
  PROCESSED_WITH_ERRORS: "Procesado con Errores",
  ERROR: "Error",
};

const normalizeStatus = (status) => String(status || "").trim().toLowerCase();

export function isCurrentProcess(row, isQueueRunning, currentProcessRowId) {
  return Boolean(
    isQueueRunning &&
      row.id != null &&
      currentProcessRowId != null &&
      String(row.id) === String(currentProcessRowId)
  );
}

export function groupProcessRows(rows, isQueueRunning, currentProcessRowId) {
  const groups = { processing: [], pending: [], completed: [] };

  for (const row of rows) {
    const status = normalizeStatus(row.estado);

    if (
      isCurrentProcess(row, isQueueRunning, currentProcessRowId) ||
      status === normalizeStatus(PROCESS_STATUS.PROCESSING)
    ) {
      groups.processing.push(row);
    } else if (status === normalizeStatus(PROCESS_STATUS.PENDING)) {
      groups.pending.push(row);
    } else {
      groups.completed.push(row);
    }
  }

  // Keep the newest-first order used by the queue worker and process history.
  groups.processing.sort(
    (a, b) =>
      Number(isCurrentProcess(b, isQueueRunning, currentProcessRowId)) -
      Number(isCurrentProcess(a, isQueueRunning, currentProcessRowId))
  );

  return groups;
}

export function getProcessStatusTone(status) {
  switch (normalizeStatus(status)) {
    case "procesado con errores":
      return "partial";
    case "procesado":
    case "completado":
      return "success";
    case "procesando":
      return "info";
    case "pendiente":
      return "warning";
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}
