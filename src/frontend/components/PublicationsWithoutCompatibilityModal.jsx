import { useEffect, useState } from "react";
import "./PublicationsWithoutCompatibilityModal.css";
import { authFetch } from "../../lib/apiClient.js";

function PublicationsWithoutCompatibilityModal({ open, onClose, apiBase }) {
  const [items, setItems] = useState([]);
  const [searchText, setSearchText] = useState("");
  const [debouncedSearchText, setDebouncedSearchText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [currentPage, setCurrentPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);

  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [exporting, setExporting] = useState(false);

  const pageSize = 20;

  useEffect(() => {
    if (!open) return;

    setCurrentPage(1);
    setSearchText("");
    setDebouncedSearchText("");
    setRefreshMessage("");
    setError("");
  }, [open]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setDebouncedSearchText(searchText.trim());
      setCurrentPage(1);
    }, 350);

    return () => clearTimeout(timeout);
  }, [searchText]);

  useEffect(() => {
    if (!open) return;

    const fetchPublications = async () => {
      try {
        setLoading(true);
        setError("");

        const params = new URLSearchParams({
          page: String(currentPage),
          page_size: String(pageSize),
        });

        if (debouncedSearchText) {
          params.append("q", debouncedSearchText);
        }

        const res = await authFetch(
          `${apiBase}/publications/without-compatibilities?${params.toString()}`,
          {
            method: "GET",
            credentials: "include",
          }
        );

        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          throw new Error(
            data?.detail ||
              data?.message ||
              "No se pudieron cargar las publicaciones sin compatibilidades."
          );
        }

        setItems(Array.isArray(data?.items) ? data.items : []);
        setTotal(Number(data?.total || 0));
        setTotalPages(Number(data?.total_pages || 0));
        setHasNext(Boolean(data?.has_next));
        setHasPrev(Boolean(data?.has_prev));
      } catch (err) {
        setError(
          err?.message ||
            "Ocurrió un error al obtener las publicaciones sin compatibilidades."
        );
        setItems([]);
        setTotal(0);
        setTotalPages(0);
        setHasNext(false);
        setHasPrev(false);
      } finally {
        setLoading(false);
      }
    };

    fetchPublications();
  }, [open, apiBase, currentPage, debouncedSearchText]);

  const handlePrevPage = () => {
    if (!hasPrev || loading) return;
    setCurrentPage((prev) => Math.max(1, prev - 1));
  };

  const handleNextPage = () => {
    if (!hasNext || loading) return;
    setCurrentPage((prev) => prev + 1);
  };

  const handleRefreshResults = async () => {
    try {
      setRefreshing(true);
      setRefreshMessage("Actualizando índice...");

      const res = await authFetch(
        `${apiBase}/publications/without-compatibilities/refresh`,
        {
          method: "POST",
          credentials: "include",
        }
      );

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new Error(
          data?.detail || data?.message || "No se pudo iniciar la actualización."
        );
      }

      const pollStatus = async () => {
        let done = false;

        while (!done) {
          const statusRes = await authFetch(
            `${apiBase}/publications/without-compatibilities/refresh-status`,
            {
              method: "GET",
              credentials: "include",
            }
          );

          const statusData = await statusRes.json().catch(() => ({}));

          if (!statusRes.ok) {
            throw new Error(
              statusData?.detail ||
                statusData?.message ||
                "No se pudo consultar el estado de actualización."
            );
          }

          if (statusData?.in_progress) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            continue;
          }

          if (statusData?.error) {
            throw new Error(statusData.error);
          }

          done = true;
        }
      };

      await pollStatus();
      setRefreshMessage("Índice actualizado correctamente.");
      setCurrentPage(1);

      setTimeout(() => {
        setRefreshMessage("");
      }, 2500);
    } catch (err) {
      setRefreshMessage(
        err?.message || "Ocurrió un error al actualizar los resultados."
      );
    } finally {
      setRefreshing(false);
    }
  };

const handleExportToExcel = async () => {
  try {
    setExporting(true);
    setError("");

    const params = new URLSearchParams();

    if (debouncedSearchText) {
      params.append("q", debouncedSearchText);
    }

    const url = `${apiBase}/publications/without-compatibilities/export${
      params.toString() ? `?${params.toString()}` : ""
    }`;

    const res = await authFetch(url, {
      method: "GET",
      credentials: "include",
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));

      let message =
        data?.detail || data?.message || "No se pudo exportar el archivo Excel.";

      if (Array.isArray(data?.detail)) {
        message = data.detail
          .map((item) => item?.msg || JSON.stringify(item))
          .join(" | ");
      } else if (data?.detail && typeof data.detail === "object") {
        message = data.detail.msg || JSON.stringify(data.detail);
      }

      throw new Error(message);
    }

    const blob = await res.blob();

    const contentDisposition = res.headers.get("Content-Disposition");
    let fileName = "publicaciones_sin_compatibilidades.xlsx";

    if (contentDisposition) {
      const match = contentDisposition.match(/filename="(.+)"/);
      if (match?.[1]) {
        fileName = match[1];
      }
    }

    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);
  } catch (err) {
    setError(
      err?.message || "Ocurrió un error al exportar las publicaciones."
    );
  } finally {
    setExporting(false);
  }
};

  if (!open) return null;

  return (
    <div className="custom-modal-overlay" onClick={onClose}>
      <div
        className="custom-modal-container publications-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="custom-modal-header">
          <h2>Publicaciones sin compatibilidades</h2>
          <button className="custom-modal-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="publications-toolbar single-search">
          <input
            type="text"
            className="publications-search-input full-width"
            placeholder="Buscar por MLC o título..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </div>

        <div className="publications-actions">
          <button
            type="button"
            className="refresh-results-button"
            onClick={handleRefreshResults}
            disabled={refreshing || exporting}
          >
            {refreshing ? "Actualizando..." : "Actualizar resultados"}
          </button>

          <button
            type="button"
            className="refresh-results-button export-results-button"
            onClick={handleExportToExcel}
            disabled={loading || exporting || refreshing || total === 0}
          >
            {exporting ? "Exportando..." : "Exportar a Excel"}
          </button>
        </div>

        <div className="publications-body">
          {loading ? (
            <div className="publications-state">Cargando publicaciones...</div>
          ) : error ? (
            <div className="publications-state error">{error}</div>
          ) : (
            <>
              <div className="publications-summary">
                <div className="publications-count">
                  Total encontrados: {total}
                </div>

                {refreshMessage && (
                  <div className="publications-refresh-message">
                    {refreshMessage}
                  </div>
                )}
              </div>

              <div className="publications-table-scroll">
                <div className="publications-table-wrapper">
                  <table className="publications-table">
                    <thead>
                      <tr>
                        <th>MLC</th>
                        <th>Título</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.length === 0 ? (
                        <tr>
                          <td colSpan="2" className="empty-row">
                            No se encontraron publicaciones.
                          </td>
                        </tr>
                      ) : (
                        items.map((item) => (
                          <tr key={item.mlc}>
                            <td>{item?.mlc || "-"}</td>
                            <td>{item?.title || "-"}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {totalPages > 0 && (
                <div className="publications-pagination">
                  <button
                    type="button"
                    onClick={handlePrevPage}
                    disabled={!hasPrev || loading}
                  >
                    Anterior
                  </button>

                  <span>
                    Página {currentPage} de {totalPages}
                  </span>

                  <button
                    type="button"
                    onClick={handleNextPage}
                    disabled={!hasNext || loading}
                  >
                    Siguiente
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default PublicationsWithoutCompatibilityModal;
