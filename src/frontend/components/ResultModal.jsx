import { useEffect, useMemo, useState } from "react";
import "./ResultModal.css";

const PAGE_SIZE = 20;

function safeText(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text || fallback;
}

function normalizeText(value) {
  return safeText(value, "").toLowerCase();
}

function flattenResults(results = []) {
  const rows = [];

  results.forEach((group, groupIndex) => {
    const brandName = safeText(group.brand_name, "Sin marca");
    const modelName = safeText(group.model_name, "Sin modelo");
    const itemId = safeText(group.item_id, `item-${groupIndex}`);
    const engineName = safeText(group.engine_name, "");
    const transmissionName = safeText(group.transmission_name, "");
    const versionName = safeText(group.version_name, "");
    const categoryId = safeText(group.category_id, "");
    const userProductId = safeText(group.user_product_id, "");
    const groupReason =
      safeText(group.reason, "") ||
      safeText(group.error_message, "") ||
      "Sin detalle";

    if (Array.isArray(group.results) && group.results.length > 0) {
      group.results.forEach((detail, detailIndex) => {
        rows.push({
          raw_key: `${itemId}-${detail.year ?? group.year ?? "na"}-${detailIndex}`,
          item_id: itemId,
          brand_name: safeText(detail.brand_name, brandName),
          model_name: safeText(detail.model_name, modelName),
          version_name: safeText(detail.version_name, versionName),
          engine_name: safeText(detail.engine_name, engineName),
          transmission_name: safeText(
            detail.transmission_name,
            transmissionName
          ),
          year:
            detail.year ??
            group.year ??
            group.year_requested ??
            group.year_processed ??
            "-",
          ok: !!detail.ok,
          product_id: safeText(detail.product_id, safeText(group.product_id, "")),
          reason:
            safeText(detail.reason, "") ||
            safeText(detail.error_message, "") ||
            (!detail.ok ? groupReason : ""),
          error_code: safeText(detail.error_code, safeText(group.error_code, "")),
          category_id: categoryId,
          user_product_id: userProductId,
        });
      });
    } else {
      rows.push({
        raw_key: `${itemId}-empty`,
        item_id: itemId,
        brand_name: brandName,
        model_name: modelName,
        version_name: versionName,
        engine_name: engineName,
        transmission_name: transmissionName,
        year:
          group.year ??
          group.year_requested ??
          group.year_processed ??
          "-",
        ok: !!group.ok,
        product_id: safeText(group.product_id, ""),
        reason: groupReason,
        error_code: safeText(group.error_code, ""),
        category_id: categoryId,
        user_product_id: userProductId,
      });
    }
  });

  return rows;
}

function buildUniqueCompatKey(row) {
  if (row.ok && row.product_id) {
    return `ok::${normalizeText(row.item_id)}::${normalizeText(row.product_id)}`;
  }

  return [
    "error",
    normalizeText(row.item_id),
    normalizeText(row.brand_name),
    normalizeText(row.model_name),
    normalizeText(row.version_name),
    normalizeText(String(row.year)),
    normalizeText(row.engine_name),
    normalizeText(row.transmission_name),
    normalizeText(row.error_code || row.reason),
  ].join("::");
}

function dedupeCompatRows(rows = []) {
  const map = new Map();

  rows.forEach((row, index) => {
    const uniqueKey = buildUniqueCompatKey(row);

    if (!map.has(uniqueKey)) {
      map.set(uniqueKey, {
        ...row,
        key: uniqueKey,
        duplicate_count: 1,
        source_rows: [index + 1],
      });
      return;
    }

    const existing = map.get(uniqueKey);
    existing.duplicate_count += 1;
    existing.source_rows.push(index + 1);

    if (!existing.product_id && row.product_id) {
      existing.product_id = row.product_id;
    }
    if (!existing.reason && row.reason) {
      existing.reason = row.reason;
    }
    if (!existing.error_code && row.error_code) {
      existing.error_code = row.error_code;
    }
  });

  return Array.from(map.values());
}

function groupRows(rows) {
  const brandMap = new Map();

  rows.forEach((row) => {
    const brandKey = safeText(row.brand_name, "Sin marca");
    const modelKey = safeText(
      row.model_name,
      row.brand_name ? `${row.brand_name} - Sin modelo` : "Sin modelo"
    );
    const itemKey = safeText(row.item_id, "Sin item");

    if (!brandMap.has(brandKey)) {
      brandMap.set(brandKey, {
        brand_name: brandKey,
        ok: 0,
        error: 0,
        total: 0,
        models: new Map(),
      });
    }

    const brand = brandMap.get(brandKey);
    brand.total += 1;
    row.ok ? brand.ok++ : brand.error++;

    if (!brand.models.has(modelKey)) {
      brand.models.set(modelKey, {
        model_name: modelKey,
        ok: 0,
        error: 0,
        total: 0,
        items: new Map(),
      });
    }

    const model = brand.models.get(modelKey);
    model.total += 1;
    row.ok ? model.ok++ : model.error++;

    if (!model.items.has(itemKey)) {
      model.items.set(itemKey, {
        item_id: itemKey,
        version_name: row.version_name,
        engine_name: row.engine_name,
        transmission_name: row.transmission_name,
        ok: 0,
        error: 0,
        total: 0,
        rows: [],
      });
    }

    const item = model.items.get(itemKey);
    item.total += 1;
    row.ok ? item.ok++ : item.error++;
    item.rows.push(row);
  });

  return Array.from(brandMap.values()).map((brand) => ({
    ...brand,
    models: Array.from(brand.models.values()).map((model) => ({
      ...model,
      items: Array.from(model.items.values()),
    })),
  }));
}

function downloadCsv(rows) {
  const headers = [
    "Marca",
    "Modelo",
    "Versión",
    "Item ID",
    "Año",
    "Estado",
    "Product ID",
    "Motivo",
    "Código Error",
    "Motor",
    "Transmisión",
    "Filas agrupadas",
  ];

  const escape = (value) => {
    const text = String(value ?? "");
    return `"${text.replaceAll('"', '""')}"`;
  };

  const csv = [
    headers.join(","),
    ...rows.map((row) =>
      [
        escape(row.brand_name),
        escape(row.model_name),
        escape(row.version_name),
        escape(row.item_id),
        escape(row.year),
        escape(row.ok ? "OK" : "ERROR"),
        escape(row.product_id),
        escape(row.reason),
        escape(row.error_code),
        escape(row.engine_name),
        escape(row.transmission_name),
        escape(row.duplicate_count ?? 1),
      ].join(",")
    ),
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "resultado_compatibilidades.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function YearStatusRow({ row }) {
  return (
    <div className={`rm-year-row ${row.ok ? "ok" : "error"}`}>
      <div className="rm-year-main">
        <div className={`rm-badge ${row.ok ? "ok" : "error"}`}>
          {row.ok ? "OK" : "Error"}
        </div>
      </div>

      <div className="rm-year-body">
        <div>
          <strong>Año:</strong> {row.year}
        </div>

        {row.version_name ? (
          <div>
            <strong>Versión:</strong> {row.version_name}
          </div>
        ) : null}

        {row.ok ? (
          <div>
            <strong>Product ID:</strong> {row.product_id || "-"}
          </div>
        ) : (
          <>
            <div>
              <strong>Motivo:</strong> {row.reason || "Sin detalle"}
            </div>
            {row.error_code ? (
              <div>
                <strong>Código:</strong> {row.error_code}
              </div>
            ) : null}
          </>
        )}

        {(row.duplicate_count ?? 1) > 1 ? (
          <div>
            <strong>Filas agrupadas:</strong> {row.duplicate_count}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ItemBlock({ item, onlyErrors }) {
  const [open, setOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const filteredRows = useMemo(() => {
    return onlyErrors ? item.rows.filter((r) => !r.ok) : item.rows;
  }, [item.rows, onlyErrors]);

  if (filteredRows.length === 0) return null;

  const visibleRows = filteredRows.slice(0, visibleCount);
  const hasMore = visibleCount < filteredRows.length;

  return (
    <div className="rm-item-block">
      <button
        className="rm-collapse-button"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        <div className="rm-collapse-left">
          <span className="rm-collapse-title">{item.item_id}</span>
          <span className="rm-collapse-meta">
            {item.version_name ? `Versión: ${item.version_name}` : ""}
            {item.version_name && item.engine_name ? " · " : ""}
            {item.engine_name ? `Motor: ${item.engine_name}` : ""}
            {(item.version_name || item.engine_name) && item.transmission_name
              ? " · "
              : ""}
            {item.transmission_name
              ? `Transmisión: ${item.transmission_name}`
              : ""}
          </span>
        </div>

        <div className="rm-collapse-right">
          <span className="rm-mini ok">OK {item.ok}</span>
          <span className="rm-mini error">Error {item.error}</span>
          <span className="rm-chevron">{open ? "▾" : "▸"}</span>
        </div>
      </button>

      {open && (
        <div className="rm-item-content">
          {visibleRows.map((row) => (
            <YearStatusRow key={row.key} row={row} />
          ))}

          {hasMore && (
            <div className="rm-load-more-wrap">
              <button
                type="button"
                className="rm-load-more"
                onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
              >
                Cargar más ({filteredRows.length - visibleCount} restantes)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ModelBlock({ model, onlyErrors }) {
  const [open, setOpen] = useState(false);

  const visibleItems = useMemo(() => {
    if (!onlyErrors) return model.items;
    return model.items.filter((item) => item.rows.some((r) => !r.ok));
  }, [model.items, onlyErrors]);

  if (visibleItems.length === 0) return null;

  return (
    <div className="rm-model-block">
      <button
        className="rm-collapse-button model"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        <div className="rm-collapse-left">
          <span className="rm-collapse-title">{model.model_name}</span>
        </div>

        <div className="rm-collapse-right">
          <span className="rm-mini ok">OK {model.ok}</span>
          <span className="rm-mini error">Error {model.error}</span>
          <span className="rm-chevron">{open ? "▾" : "▸"}</span>
        </div>
      </button>

      {open && (
        <div className="rm-model-content">
          {visibleItems.map((item) => (
            <ItemBlock
              key={item.item_id}
              item={item}
              onlyErrors={onlyErrors}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function BrandBlock({ brand, onlyErrors }) {
  const [open, setOpen] = useState(false);

  const visibleModels = useMemo(() => {
    if (!onlyErrors) return brand.models;
    return brand.models.filter((model) =>
      model.items.some((item) => item.rows.some((r) => !r.ok))
    );
  }, [brand.models, onlyErrors]);

  if (visibleModels.length === 0) return null;

  return (
    <div className="rm-brand-block">
      <button
        className="rm-collapse-button brand"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        <div className="rm-collapse-left">
          <span className="rm-collapse-title">{brand.brand_name}</span>
          <span className="rm-collapse-meta">
            {brand.total} compatibilidades únicas
          </span>
        </div>

        <div className="rm-collapse-right">
          <span className="rm-mini ok">OK {brand.ok}</span>
          <span className="rm-mini error">Error {brand.error}</span>
          <span className="rm-chevron">{open ? "▾" : "▸"}</span>
        </div>
      </button>

      {open && (
        <div className="rm-brand-content">
          {visibleModels.map((model) => (
            <ModelBlock
              key={`${brand.brand_name}-${model.model_name}`}
              model={model}
              onlyErrors={onlyErrors}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ResultModal({ open, onClose, summary, results }) {
  const [search, setSearch] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [statusFilter, setStatusFilter] = useState("ok");

  useEffect(() => {
    if (open) {
      setStatusFilter("ok");
      setOnlyErrors(false);
      setSearch("");
    }
  }, [open]);

  const rawRows = useMemo(() => flattenResults(results || []), [results]);
  const compatRows = useMemo(() => dedupeCompatRows(rawRows), [rawRows]);

  const filteredRows = useMemo(() => {
    const term = search.trim().toLowerCase();

    return compatRows.filter((row) => {
      const matchesOnlyErrors = onlyErrors ? !row.ok : true;

      const matchesStatus =
        statusFilter === "ok"
          ? row.ok
          : statusFilter === "error"
          ? !row.ok
          : true;

      const haystack = [
        row.brand_name,
        row.model_name,
        row.version_name,
        row.item_id,
        row.year,
        row.product_id,
        row.reason,
        row.error_code,
      ]
        .join(" ")
        .toLowerCase();

      const matchesSearch = term ? haystack.includes(term) : true;

      return matchesOnlyErrors && matchesStatus && matchesSearch;
    });
  }, [compatRows, onlyErrors, statusFilter, search]);

  const grouped = useMemo(() => groupRows(filteredRows), [filteredRows]);

  const computedSummary = useMemo(() => {
    const total = compatRows.length;
    const ok = compatRows.filter((r) => r.ok).length;
    const error = compatRows.filter((r) => !r.ok).length;

    const brands = new Set(
      compatRows
        .map((r) => safeText(r.brand_name, "").toLowerCase())
        .filter(Boolean)
    ).size;

    const models = new Set(
      compatRows
        .map((r) =>
          `${safeText(r.brand_name, "").toLowerCase()}__${safeText(
            r.model_name,
            ""
          ).toLowerCase()}`
        )
        .filter((v) => !v.endsWith("__"))
    ).size;

    return { total, ok, error, brands, models };
  }, [compatRows]);

  if (!open) return null;

  const processedRows =
    summary?.processed_rows ??
    summary?.total_rows ??
    summary?.processed ??
    rawRows.length;

  const totalCompatibilities = computedSummary.total;
  const compatibilitiesOk = computedSummary.ok;
  const compatibilitiesError = computedSummary.error;
  const brandsCount = computedSummary.brands;
  const modelsCount = computedSummary.models;

  const handleStatusCardClick = (nextFilter) => {
    setOnlyErrors(false);

    setStatusFilter((current) => {
      if (current === nextFilter) {
        return "all";
      }
      return nextFilter;
    });
  };

  return (
    <div className="rm-overlay">
      <div className="rm-modal">
        <div className="rm-header">
          <h2>Resultado del procesamiento</h2>
          <button className="rm-close" onClick={onClose} type="button">
            ✕
          </button>
        </div>

        <div className="rm-body">
          <div className="rm-summary-grid">
            <div className="rm-summary-card neutral">
              <span>Filas procesadas</span>
              <strong>{processedRows}</strong>
            </div>

            <div className="rm-summary-card neutral">
              <span>Total compatibilidades</span>
              <strong>{totalCompatibilities}</strong>
            </div>

            <button
              type="button"
              className={`rm-summary-card success clickable ${
                statusFilter === "ok" ? "active" : ""
              }`}
              onClick={() => handleStatusCardClick("ok")}
            >
              <span>Compatibilidades OK</span>
              <strong>{compatibilitiesOk}</strong>
            </button>

            <button
              type="button"
              className={`rm-summary-card error clickable ${
                statusFilter === "error" ? "active" : ""
              }`}
              onClick={() => handleStatusCardClick("error")}
            >
              <span>Compatibilidades con error</span>
              <strong>{compatibilitiesError}</strong>
            </button>

            <div className="rm-summary-card info">
              <span>Marcas</span>
              <strong>{brandsCount}</strong>
            </div>

            <div className="rm-summary-card info">
              <span>Modelos</span>
              <strong>{modelsCount}</strong>
            </div>
          </div>

          <div className="rm-toolbar">
            <input
              className="rm-search"
              type="text"
              placeholder="Buscar marca, modelo, item, año, motivo..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />

            <label className="rm-checkbox">
              <input
                type="checkbox"
                checked={onlyErrors}
                onChange={(e) => {
                  const checked = e.target.checked;
                  setOnlyErrors(checked);
                  if (checked) {
                    setStatusFilter("all");
                  }
                }}
              />
              Mostrar solo errores
            </label>

            <button
              type="button"
              className="rm-export"
              onClick={() => downloadCsv(filteredRows)}
            >
              Descargar CSV
            </button>
          </div>

          <div className="rm-results-meta">
            Mostrando {filteredRows.length} compatibilidad(es) única(s)
            {search ? ` para "${search}"` : ""}
            {statusFilter === "ok" ? " · solo OK" : ""}
            {statusFilter === "error" ? " · solo errores" : ""}
            {onlyErrors ? " · filtro adicional: solo errores" : ""}
          </div>

          <div className="rm-results-container">
            {grouped.length === 0 ? (
              <div className="rm-empty">
                No hay resultados para los filtros seleccionados.
              </div>
            ) : (
              grouped.map((brand) => (
                <BrandBlock
                  key={brand.brand_name}
                  brand={brand}
                  onlyErrors={onlyErrors}
                />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}