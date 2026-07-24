import { useState } from "react";
import { CalendarDays, CloudDownload, Download } from "lucide-react";
import "../styles/DownloadPublications.css";

const getLocalToday = () => {
  const today = new Date();
  const timezoneOffset = today.getTimezoneOffset() * 60_000;

  return new Date(today.getTime() - timezoneOffset)
    .toISOString()
    .slice(0, 10);
};

export default function DownloadPublications() {
  const [publicationDate, setPublicationDate] = useState(getLocalToday);

  return (
    <section className="download-publications-page">
      <div className="download-publications-layout">
        <header className="download-publications-header">
          <h1>Descargar Publicaciones</h1>
          <p>
            Carga las publicaciones disponibles y descarga la información de la
            fecha seleccionada.
          </p>
        </header>

        <div className="download-publications-card">
          <div className="download-publications-load-section">
            <span className="download-publications-section-label">
              Publicaciones de Mercado Libre
            </span>

            <button
              type="button"
              className="download-publications-load-button"
            >
              <CloudDownload size={20} aria-hidden="true" />
              Cargar Publicaciones
            </button>
          </div>

          <div
            className="download-publications-divider"
            aria-hidden="true"
          />

          <div className="download-publications-filter-row">
            <div className="download-publications-date-field">
              <label htmlFor="publication-date">
                Fecha de Publicaciones
              </label>

              <div className="download-publications-date-control">
                <CalendarDays size={19} aria-hidden="true" />
                <input
                  id="publication-date"
                  type="date"
                  value={publicationDate}
                  onChange={(event) => setPublicationDate(event.target.value)}
                />
              </div>
            </div>

            <button
              type="button"
              className="download-publications-download-button"
            >
              <Download size={19} aria-hidden="true" />
              Descargar Publicaciones
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
