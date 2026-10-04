import { useLocation } from "react-router-dom";
import { Bell, Menu } from "lucide-react";
import useAccountStatus from "../hooks/useAccountStatus.js";

const ROUTE_LABELS = {
  "/menu": ["Inicio"],
  "/procesos/sincronizacion-procesos": ["Procesos", "Sincronizacion"],
  "/procesos/carga-familias-compatibilidades": [
    "Procesos",
    "Carga de Familias",
  ],
  "/procesos/copia-compatibilidades": ["Procesos", "Copia de Compatibilidades"],
  "/procesos/formatos-excel": ["Procesos", "Formatos Excel"],
  "/compatibilidades/carga-masiva": ["Compatibilidades", "Carga Masiva"],
  "/compatibilidades/no-compatibilidades": [
    "Compatibilidades",
    "No Compatibilidades",
  ],
  "/actualizaciones/precios-stock": ["Actualizaciones", "Precios y Stock"],
  "/gestion/descargar-publicaciones": ["Gestion", "Descargar Publicaciones"],
  "/gestion/devoluciones": ["Gestion", "Devoluciones"],
  "/gestion/ventas": ["Gestion", "Gestión de Ventas"],
  "/integraciones/proveedores": ["Integraciones", "Integración Proveedores"],
};

export default function TopMenuBar({ onOpenSidebar }) {
  const location = useLocation();
  const { userLabel, mlStatusLabel, mlStatusLoading, isMlConnected } = useAccountStatus();
  const breadcrumbs = ROUTE_LABELS[location.pathname] || ["Repnet"];

  return (
    <header className="top-menu-bar">
      <button
        type="button"
        className="top-menu-bar__menu-button"
        onClick={onOpenSidebar}
        aria-label="Abrir menu"
      >
        <Menu size={22} />
      </button>

      <nav className="top-menu-bar__breadcrumbs" aria-label="Ruta actual">
        {breadcrumbs.map((crumb, index) => (
          <span
            key={`${crumb}-${index}`}
            className={
              index === breadcrumbs.length - 1
                ? "top-menu-bar__breadcrumb top-menu-bar__breadcrumb--current"
                : "top-menu-bar__breadcrumb"
            }
          >
            {crumb}
          </span>
        ))}
      </nav>

      <div className="top-menu-bar__meta">
        <div className="top-menu-bar__item">
          <span className="top-menu-bar__label">Estado Mercado Libre</span>
          <span
            className={`top-menu-bar__status ${
              mlStatusLoading
                ? "top-menu-bar__status--pending"
                : isMlConnected
                ? "top-menu-bar__status--success"
                : "top-menu-bar__status--danger"
            }`}
          >
            {mlStatusLabel}
          </span>
        </div>

        <span className="top-menu-bar__divider" aria-hidden="true" />

        <span className="top-menu-bar__value" title={userLabel}>
          {userLabel}
        </span>

        <button
          type="button"
          className="top-menu-bar__notification"
          aria-label="Notificaciones"
        >
          <Bell size={18} />
        </button>
      </div>
    </header>
  );
}
