import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("C:/Users/Rodrigo Araya/AppData/Local/npm-cache/_npx/705bc6b22212b352/node_modules/playwright");

const artifactDir = "C:/RepnetPlatform1.0/artifacts/catalogo-emilia";
const browser = await chromium.launch({ headless: true, executablePath: "C:/Users/Rodrigo Araya/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe" });
const page = await browser.newPage({ viewport: { width: 1873, height: 935 }, acceptDownloads: true });
const errors = [];
const starts = [];
const jobs = new Map([["date-ready", {
  job_id: "date-ready", status: "success", progress: 100, publication_date: "2026-10-06",
  export_scope: "date", total_rows: 1675, processed_rows: 1675, retry_count: 0,
  filename: "publicaciones_2026-10-06.xlsx", download_ready: true,
  message: "Excel listo: 1675 publicaciones exportadas desde la base de datos.",
}]]);
let failNextCatalog = false;
let downloadCount = 0;
const statusRequests = [];
const pendingRequests = [];
let holdNextRequest = "";
page.on("pageerror", error => errors.push(error.message));
page.on("download", () => downloadCount++);
await page.addInitScript(() => {
  if (!localStorage.getItem("preview-initialized")) {
    localStorage.setItem("repnet_publication_export_job_id:preview-user", "date-ready");
    localStorage.setItem("repnet_publication_export_downloaded_job_id:preview-user", "date-ready");
    localStorage.setItem("repnet_catalog_export_job_id:preview-user", "catalog-failed");
    localStorage.setItem("repnet_catalog_export_downloaded_job_id:preview-user", "catalog-failed");
    localStorage.setItem("preview-initialized", "true");
  }
});
await page.route("**/src/lib/supabase.js*", route => route.fulfill({
  contentType: "application/javascript",
  body: `export const isSupabaseConfigured = true;
    export const supabaseConfigErrorMessage = '';
    export const supabase = { auth: {
      getSession: async () => ({ data: { session: null } }),
      getUser: async () => ({ data: { user: { id: 'preview-user', email: 'vista.previa@repnet.cl' } } }),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } })
    } };`,
}));
await page.route("**/ml/status", route => route.fulfill({ json: { connected: true } }));
await page.route("**/publications/**", async route => {
  const request = route.request();
  const path = new URL(request.url()).pathname;
  if (path.endsWith("/sync/status")) {
    return route.fulfill({ json: { status: "idle", running: false, has_publications: true } });
  }
  if (path.endsWith("/export") && request.method() === "POST") {
    const payload = request.postDataJSON();
    starts.push(payload);
    if (failNextCatalog && payload.export_scope === "catalog") {
      failNextCatalog = false;
      return route.fulfill({ status: 404, json: { detail: "No existen publicaciones guardadas para el catálogo completo." } });
    }
    const scope = payload.export_scope || "date";
    if (holdNextRequest === "start") {
      holdNextRequest = "";
      await new Promise(resolve => pendingRequests.push(resolve));
    }
    const id = `${scope}-${starts.length}`;
    const job = {
      job_id: id, status: "queued", progress: 0, export_scope: scope,
      publication_date: payload.publication_date || null,
      total_rows: scope === "catalog" ? 1005 : 3, processed_rows: 0, retry_count: 0,
      filename: scope === "catalog" ? "catalogo_completo_emilia.xlsx" : `publicaciones_${payload.publication_date}.xlsx`,
      download_ready: false, message: "Exportación encolada.", polls: 0,
    };
    jobs.set(id, job);
    return route.fulfill({ json: job });
  }
  const parts = path.split("/");
  const id = path.endsWith("/download") ? parts.at(-2) : parts.at(-1);
  const job = jobs.get(id);
  if (!job) return route.fulfill({ status: 404, json: { detail: "Exportación no encontrada." } });
  if (path.endsWith("/download")) {
    if (holdNextRequest === "download") {
      holdNextRequest = "";
      await new Promise(resolve => pendingRequests.push(resolve));
    }
    return route.fulfill({
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      body: await readFile(`${artifactDir}/browser-fixture.xlsx`),
    });
  }
  statusRequests.push(id);
  if (holdNextRequest === "status") {
    holdNextRequest = "";
    await new Promise(resolve => pendingRequests.push(resolve));
  }
  if (job.status !== "success") {
    job.polls++;
    Object.assign(job, job.polls === 1
      ? { status: "processing", progress: 65, processed_rows: job.total_rows, message: "Construyendo Excel..." }
      : { status: "success", progress: 100, processed_rows: job.total_rows, download_ready: true,
          message: `Excel listo: ${job.total_rows} publicaciones exportadas desde la base de datos.` });
  }
  return route.fulfill({ json: job });
});

try {
  const url = "http://127.0.0.1:5174/artifacts/catalogo-emilia/preview.html";
  await page.goto(url);
  const catalog = page.getByRole("region", { name: "Catálogo completo Emilia" });
  const catalogButton = catalog.getByRole("button", { name: "Descargar Catalogo Completo Emilia" });
  const dateButton = page.locator(".download-publications-download-button");
  const assertClean = async () => {
    await page.waitForFunction(() => {
      const buttons = [...document.querySelectorAll(".download-publications-filter-row button, .download-publications-catalog-button")];
      return buttons.length === 2 && buttons.every(button => !button.disabled)
        && document.querySelectorAll(".download-publications-export-status").length === 0;
    });
    assert.equal(await dateButton.innerText(), "Descargar Publicaciones");
    const savedReferences = await page.evaluate(() => Object.keys(localStorage).filter(key =>
      key.startsWith("repnet_publication_export") || key.startsWith("repnet_catalog_export")
    ));
    assert.deepEqual(savedReferences, []);
  };
  const reenter = async () => {
    await page.getByRole("link", { name: "Integración Proveedores" }).click();
    await page.getByText("Otra pantalla de la vista previa").waitFor();
    await page.getByRole("link", { name: "Descargar Publicaciones", exact: true }).click();
    await catalogButton.waitFor();
    await assertClean();
  };
  await catalogButton.waitFor();
  await assertClean();
  assert.deepEqual(statusRequests, []);
  const cardBoxes = await page.locator(".download-publications-card").evaluateAll(cards => cards.map(c => {
    const box = c.getBoundingClientRect();
    return { top: box.top, bottom: box.bottom };
  }));
  assert(cardBoxes[1].top - cardBoxes[0].bottom >= 30);
  assert.equal(await catalogButton.evaluate(button => getComputedStyle(button).backgroundColor), "rgb(255, 230, 0)");
  await page.screenshot({ path: `${artifactDir}/escritorio.png`, fullPage: true });

  const catalogDownload = page.waitForEvent("download");
  await catalogButton.evaluate(button => { button.click(); button.click(); });
  await catalog.getByRole("button", { name: "Generando catálogo completo..." }).waitFor();
  assert(await dateButton.isDisabled());
  const downloadedCatalog = await catalogDownload;
  assert.equal(downloadedCatalog.suggestedFilename(), "catalogo_completo_emilia.xlsx");
  assert.equal(starts.length, 1);
  assert.deepEqual(starts[0], { export_scope: "catalog", refresh: true });
  await catalog.getByText("Excel listo: 1005 publicaciones exportadas desde la base de datos.").waitFor();
  await page.screenshot({ path: `${artifactDir}/catalogo-listo.png`, fullPage: true });

  await page.reload();
  await assertClean();
  assert.equal(downloadCount, 1);

  await page.locator("#publication-date").fill("2026-10-05");
  const dateDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar Publicaciones", exact: true }).click();
  const downloadedDate = await dateDownload;
  assert.equal(downloadedDate.suggestedFilename(), "publicaciones_2026-10-05.xlsx");
  assert.deepEqual(starts[1], { publication_date: "2026-10-05", refresh: true });
  assert.equal(await catalog.locator(".download-publications-export-status").count(), 0);

  const nextCatalogDownload = page.waitForEvent("download");
  await catalogButton.click();
  await nextCatalogDownload;
  await catalog.getByText("Excel listo: 1005 publicaciones exportadas desde la base de datos.").waitFor();
  assert(await page.getByRole("button", { name: "Descargar Excel", exact: true }).isEnabled());
  await page.getByRole("link", { name: "Descargar Publicaciones", exact: true }).click();
  await assertClean();
  await reenter();
  const repeatedDateDownload = page.waitForEvent("download");
  await page.locator("#publication-date").fill("2026-10-05");
  await dateButton.click();
  await repeatedDateDownload;
  assert.deepEqual(starts[3], { publication_date: "2026-10-05", refresh: true });

  failNextCatalog = true;
  await catalogButton.click();
  await catalog.getByText("No existen publicaciones guardadas para el catálogo completo.").waitFor();
  assert(await page.getByRole("button", { name: "Descargar Excel", exact: true }).isEnabled());
  await reenter();
  const retryDownload = page.waitForEvent("download");
  await catalogButton.click();
  await retryDownload;
  await catalog.getByText("Excel listo: 1005 publicaciones exportadas desde la base de datos.").waitFor();
  await page.waitForFunction(() => document.querySelector(".download-publications-catalog-button")?.disabled === false);
  assert.equal(starts.filter(body => body.export_scope === "catalog").length, 4);

  for (const pendingPhase of ["start", "status", "download"]) {
    await reenter();
    const previousDownloads = downloadCount;
    holdNextRequest = pendingPhase;
    await catalogButton.click();
    const pendingDeadline = Date.now() + 5000;
    while (pendingRequests.length === 0) {
      assert(Date.now() < pendingDeadline, `No se inició la solicitud pendiente de ${pendingPhase}.`);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    await reenter();
    pendingRequests.shift()();
    // A late response from the previous visit must not revive its UI or download.
    await new Promise(resolve => setTimeout(resolve, 100));
    await assertClean();
    assert.equal(downloadCount, previousDownloads);
  }

  const finalDownload = page.waitForEvent("download");
  await catalogButton.click();
  await finalDownload;
  await catalog.getByText("Excel listo: 1005 publicaciones exportadas desde la base de datos.").waitFor();
  await reenter();
  await page.screenshot({ path: `${artifactDir}/entrada-limpia.png`, fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".sidebar").evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)));
  await page.locator(".download-publications-page").evaluate(el => { el.scrollTop = 0; });
  const mobile = await page.locator(".download-publications-page").evaluate(el => ({
    clientWidth: el.clientWidth, scrollWidth: el.scrollWidth,
  }));
  assert.equal(mobile.scrollWidth, mobile.clientWidth);
  const buttonBox = await catalogButton.boundingBox();
  assert(buttonBox.x >= 0 && buttonBox.x + buttonBox.width <= 390);
  await page.setViewportSize({ width: 390, height: 1200 });
  await page.screenshot({ path: `${artifactDir}/movil.png`, fullPage: true });
  assert.deepEqual(errors, []);
  const verification = {
    passed: true, scenarios: ["tarjetas separadas", "paleta Mercado Libre", "descarga automática",
      "doble clic sin duplicados", "fecha y catálogo independientes", "limpieza al recargar",
      "limpieza al salir y volver", "limpieza al seleccionar otra vez la misma opción del menú",
      "limpieza de referencias antiguas de ambas descargas",
      "error y reintento", "respuestas tardías de inicio, progreso y descarga ignoradas",
      "nueva exportación por fecha después de volver", "escritorio y móvil sin desbordamiento"],
    requestBodies: starts, browserErrors: errors,
    api: "Respuestas simuladas; el Excel real se verifica por separado con Supabase.",
  };
  await writeFile(`${artifactDir}/verificacion-navegador.json`, JSON.stringify(verification, null, 2));
  console.log(JSON.stringify(verification));
} finally {
  await browser.close();
}
