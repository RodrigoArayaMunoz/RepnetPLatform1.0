export const RETURNS_TIME_ZONE = "America/Santiago";

const dayFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: RETURNS_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const displayFormatter = new Intl.DateTimeFormat("es-CL", {
  timeZone: RETURNS_TIME_ZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function getChileDateKey(date) {
  const parts = Object.fromEntries(
    dayFormatter.formatToParts(date).map(({ type, value }) => [type, value])
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function getDeadlineStatus(deadlineValue, now = new Date()) {
  if (!deadlineValue) return { key: "unknown", label: "Sin plazo ML" };
  const deadline = new Date(deadlineValue);
  if (Number.isNaN(deadline.getTime())) return { key: "unknown", label: "Sin plazo ML" };

  const today = getChileDateKey(now);
  const deadlineDay = getChileDateKey(deadline);
  if (deadlineDay < today) return { key: "overdue", label: "Vencido" };
  if (deadlineDay === today) return { key: "today", label: "Vence hoy" };

  const tomorrow = new Date(now);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  if (deadlineDay === getChileDateKey(tomorrow)) {
    return { key: "tomorrow", label: "Vence mañana" };
  }
  return { key: "later", label: "Con plazo" };
}

export function formatChileDateTime(value) {
  if (!value) return "Pendiente de Mercado Libre";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Pendiente de Mercado Libre";
  const parts = Object.fromEntries(
    displayFormatter.formatToParts(date).map(({ type, value: part }) => [type, part])
  );
  return `${parts.day}/${parts.month}/${parts.year} - ${parts.hour}:${parts.minute}`;
}

export function formatChileClock(value) {
  const parts = Object.fromEntries(
    displayFormatter.formatToParts(value).map(({ type, value: part }) => [type, part])
  );
  return `${parts.day}/${parts.month}/${parts.year} - ${parts.hour}:${parts.minute}:${parts.second}`;
}

export function isPendingReturn(row) {
  const state = String(row?.estado || "").trim().toLowerCase();
  return !/^(cerrad|finalizad|completad|resuelt|revisad)/.test(state);
}
