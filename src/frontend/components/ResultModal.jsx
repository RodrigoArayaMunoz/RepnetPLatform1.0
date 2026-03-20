import { useEffect, useMemo, useState } from "react";
import "./ResultModal.css";

const PAGE_SIZE = 20;



function flattenResults(results = []) {
  const rows = [];

  results.forEach((group, groupIndex) => {
    const brandName = group.brand_name || "Sin marca";
    const modelName = group.model_name || "Sin modelo";
    const itemId = group.item_id || `item-${groupIndex}`;
    const engineName = group.engine_name || "";
    const transmissionName = group.transmission_name || "";

    if (Array.isArray(group.results) && group.results.length > 0) {
      group.results.forEach((detail, detailIndex) => {
        rows.push({
          key: `${itemId}-${detail.year ?? "na"}-${detailIndex}`,
          item_id: itemId,
          brand_name: detail.brand_name || brandName,
          model_name: detail.model_name || modelName,
          engine_name: engineName,
          transmission_name: transmissionName,
          year: detail.year ?? "-",
          ok: !!detail.ok,
          product_id: detail.product_id || "",
          reason: detail.reason || "",
          category_id: group.category_id || "",
          user_product_id: group.user_product_id || "",
        });
      });
    } else {
      rows.push({
        key: `${itemId}-empty`,
        item_id: itemId,
        brand_name: brandName,
        model_name: modelName,
        engine_name: engineName,
        transmission_name: transmissionName,
        year: "-",
        ok: !!group.ok,
        product_id: "",
        reason: group.reason || "Sin detalle",
        category_id: group.category_id || "",
        user_product_id: group.user_product_id || "",
      });
    }
  });

  return rows;
}

function groupRows(rows) {
  const brandMap = new Map();

  rows.forEach((row) => {
    const brandKey = row.brand_name || "Sin marca";
    const modelKey = row.model_name || "Sin modelo";
    const itemKey = row.item_id || "Sin item";

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
    "Item ID",
    "Año",
    "Estado",
    "Product ID",
    "Motivo",
    "Motor",
    "Transmisión",
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
        escape(row.item_id),
        escape(row.year),
        escape(row.ok ? "OK" : "ERROR"),
        escape(row.product_id),
        escape(row.reason),
        escape(row.engine_name),
        escape(row.transmission_name),
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
        {row.ok ? (
          <span>
            <strong>Product ID:</strong> {row.product_id}
          </span>
        ) : (
          <span>
            <strong>Motivo:</strong> {row.reason || "Sin detalle"}
          </span>
        )}
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
            {item.engine_name ? `Motor: ${item.engine_name}` : ""}
            {item.engine_name && item.transmission_name ? " · " : ""}
            {item.transmission_name ? `Transmisión: ${item.transmission_name}` : ""}
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
            {brand.total} compatibilidades
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

  const flatRows = useMemo(() => flattenResults(results || []), [results]);

const filteredRows = useMemo(() => {
  const term = search.trim().toLowerCase();

  return flatRows.filter((row) => {
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
      row.item_id,
      row.year,
      row.product_id,
      row.reason,
    ]
      .join(" ")
      .toLowerCase();

    const matchesSearch = term ? haystack.includes(term) : true;

    return matchesOnlyErrors && matchesStatus && matchesSearch;
  });
}, [flatRows, onlyErrors, statusFilter, search]);

  const grouped = useMemo(() => groupRows(filteredRows), [filteredRows]);

  const computedSummary = useMemo(() => {
    const total = flatRows.length;
    const ok = flatRows.filter((r) => r.ok).length;
    const error = flatRows.filter((r) => !r.ok).length;
    const brands = new Set(flatRows.map((r) => r.brand_name)).size;
    const models = new Set(
      flatRows.map((r) => `${r.brand_name}__${r.model_name}`)
    ).size;

    return { total, ok, error, brands, models };
  }, [flatRows]);

  if (!open) return null;

  const totalProcessed =
    summary?.processed_rows ?? summary?.processed ?? computedSummary.total;

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
              <strong>{summary?.processed_rows ?? totalProcessed}</strong>
            </div>

            <div className="rm-summary-card neutral">
              <span>Total compatibilidades</span>
              <strong>{summary?.compatibilities_total ?? computedSummary.total}</strong>
            </div>

<button
  type="button"
  className={`rm-summary-card success clickable ${
    statusFilter === "ok" ? "active" : ""
  }`}
  onClick={() => handleStatusCardClick("ok")}
>
  <span>Compatibilidades OK</span>
  <strong>{summary?.compatibilities_ok ?? computedSummary.ok}</strong>
</button>

<button
  type="button"
  className={`rm-summary-card error clickable ${
    statusFilter === "error" ? "active" : ""
  }`}
  onClick={() => handleStatusCardClick("error")}
>
  <span>Compatibilidades con error</span>
  <strong>{summary?.compatibilities_error ?? computedSummary.error}</strong>
</button>

            <div className="rm-summary-card info">
              <span>Marcas</span>
              <strong>{computedSummary.brands}</strong>
            </div>

            <div className="rm-summary-card info">
              <span>Modelos</span>
              <strong>{computedSummary.models}</strong>
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
  Mostrando {filteredRows.length} resultado(s)
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