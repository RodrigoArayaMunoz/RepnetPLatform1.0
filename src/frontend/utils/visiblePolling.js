const TERMINAL_STATUS = new Set([400, 401, 402, 403, 404, 410, 422]);

export function pollingError(response, message) {
  const error = new Error(message);
  error.status = response.status;
  const retryAfter = response.headers?.get?.("Retry-After");
  const seconds = Number(retryAfter);
  error.retryAfterMs = retryAfter
    ? Math.max(0, Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - Date.now())
    : 0;
  return error;
}

// One request at a time; hidden tabs do no work. Failures eventually require a
// manual refresh instead of keeping a dead session or endpoint busy forever.
export function createVisiblePoller({
  run, intervalMs = 5000, onError = () => {}, maxFailures = 5,
  visibility = document, schedule = setTimeout, cancel = clearTimeout,
  now = Date.now,
}) {
  let timer;
  let controller;
  let stopped = false;
  let halted = false;
  let active = false;
  let wakePending = false;
  let failures = 0;
  let retryNotBefore = 0;

  const visible = () => visibility.visibilityState !== "hidden";
  const later = (delay) => {
    cancel(timer);
    if (!stopped && !halted && visible()) timer = schedule(tick, Math.max(delay, retryNotBefore - now()));
  };
  const tick = async () => {
    if (stopped || halted || active || !visible()) return;
    active = true;
    controller = new AbortController();
    let delay = intervalMs;
    try {
      const next = await run(controller.signal);
      failures = 0;
      retryNotBefore = 0;
      if (next === false) halted = true;
      else if (Number.isFinite(next)) delay = Math.max(0, next);
    } catch (error) {
      if (!controller.signal.aborted && !stopped) {
        failures += 1;
        halted = TERMINAL_STATUS.has(Number(error.status)) || failures >= maxFailures;
        error.pollingStopped = halted;
        delay = Math.max(
          Math.min(intervalMs * 2 ** failures, 60_000),
          Number(error.retryAfterMs) || 0,
        );
        retryNotBefore = now() + delay;
        onError(error);
      }
    } finally {
      active = false;
      later(wakePending ? 0 : delay);
      wakePending = false;
    }
  };
  const onVisibility = () => {
    cancel(timer);
    if (!visible()) {
      controller?.abort();
    } else if (active) {
      wakePending = true;
    } else {
      later(0);
    }
  };
  visibility.addEventListener("visibilitychange", onVisibility);
  later(0);
  return () => {
    stopped = true;
    cancel(timer);
    controller?.abort();
    visibility.removeEventListener("visibilitychange", onVisibility);
  };
}

// Observes an already started import. Cancelling observation never cancels or
// resubmits the server-side job; hidden tabs resume with the same job id.
export function waitForVisibleJob({ read, onProgress, signal, ...options }) {
  return new Promise((resolve, reject) => {
    let stop = () => {};
    const finish = (error, data) => {
      stop();
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve(data);
    };
    const onAbort = () => finish(new DOMException("Seguimiento cancelado", "AbortError"));
    if (signal?.aborted) { onAbort(); return; }
    signal?.addEventListener("abort", onAbort, { once: true });
    stop = createVisiblePoller({
      ...options,
      intervalMs: 5000,
      run: async (requestSignal) => {
        const data = await read(requestSignal);
        if (requestSignal.aborted || signal?.aborted) return;
        onProgress(data);
        if (data.status === "success") {
          finish(null, data);
          return false;
        }
        if (data.status === "error") {
          finish(new Error(data.message || "El proceso terminó con errores."));
          return false;
        }
      },
      onError: (error) => {
        if (error.pollingStopped) finish(error);
      },
    });
  });
}
