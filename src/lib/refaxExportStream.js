const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export async function readRefaxExport(response, { onProgress, signal } = {}) {
  if (!response.body) throw new Error("No se pudo recibir el progreso de la exportación.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const chunks = [];
  let buffer = "";
  let file = null;
  let received = 0;
  let completed = false;
  let percentage = 0;

  const report = (event) => {
    percentage = Math.max(percentage, Math.min(99, event.percentage));
    onProgress?.({ ...event, percentage });
  };

  const handle = (line) => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") throw new Error(event.message || "No se pudo generar el Excel.");
    if (event.type === "progress") {
      if (!Number.isFinite(event.percentage)) throw new Error("Progreso de exportación inválido.");
      report(event);
    } else if (event.type === "file") {
      if (file || event.content_type !== XLSX_TYPE || !Number.isSafeInteger(event.size) || event.size <= 0) {
        throw new Error("El servidor no entregó un archivo Excel válido.");
      }
      file = event;
    } else if (event.type === "chunk") {
      if (!file || completed) throw new Error("La descarga del Excel está incompleta.");
      const binary = atob(event.data);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      received += bytes.length;
      if (received > file.size) throw new Error("El tamaño del Excel recibido es incorrecto.");
      chunks.push(bytes);
      report({ stage: "downloading", percentage: 95 + Math.floor(5 * received / file.size),
        message: "Recibiendo el archivo Excel…" });
    } else if (event.type === "complete") {
      if (!file || received !== file.size) throw new Error("La descarga del Excel está incompleta.");
      completed = true;
    }
  };

  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        handle(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) handle(buffer);
    if (!completed) throw new Error("Se interrumpió la exportación. Vuelve a intentarlo.");
    onProgress?.({ stage: "complete", percentage: 100, message: "Excel listo." });
    return { blob: new Blob(chunks, { type: XLSX_TYPE }), filename: file.filename || "productos_refax.xlsx" };
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}
