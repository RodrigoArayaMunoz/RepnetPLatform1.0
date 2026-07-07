import "../styles/ManagementPlaceholder.css";

export default function ManagementPlaceholder({ title }) {
  return (
    <section className="management-placeholder">
      <div className="management-placeholder__layout">
        <header className="management-placeholder__header">
          <h1>{title}</h1>
          <p>Modulo de gestion disponible para configuracion operativa.</p>
        </header>

        <div className="management-placeholder__card">
          <span className="management-placeholder__status">En preparacion</span>
          <p>
            Esta pantalla esta lista para recibir el flujo de trabajo del modulo.
          </p>
        </div>
      </div>
    </section>
  );
}
