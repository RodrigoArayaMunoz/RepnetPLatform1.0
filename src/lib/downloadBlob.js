// El porcentaje corresponde al archivo recibido, no al tiempo de preparación.
export async function readDownloadBlob(response, { onProgress, signal } = {}) {
  const length = Number(response.headers.get("content-length"));
  const total = Number.isFinite(length) && length > 0 ? length : null;
  const contentType = response.headers.get("content-type") || "application/octet-stream";
  const report = (percentage) => onProgress?.({ stage: "downloading", percentage });
  signal?.throwIfAborted();
  report(total ? 0 : null);

  if (!response.body) {
    const blob = await response.blob();
    signal?.throwIfAborted();
    report(100);
    return blob;
  }

  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      if (done) break;
      chunks.push(value);
      received += value.byteLength;
      // Reservar 100% hasta que el stream haya terminado sin errores.
      report(total ? Math.min(99, Math.floor((received / total) * 100)) : null);
    }
    const blob = new Blob(chunks, { type: contentType });
    report(100);
    return blob;
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}
