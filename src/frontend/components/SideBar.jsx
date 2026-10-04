import { NavLink, useLocation } from "react-router-dom";
import {
  X,
  Boxes,
  BadgeDollarSign,
  FileSpreadsheet,
  Ban,
  CopyPlus,
  FolderSync,
  FolderTree,
  Download,
  Undo2,
  ShoppingCart,
  Plug,
} from "lucide-react";
import logo from "../../assets/repnetsolo_logo.png";
import useAccountStatus from "../hooks/useAccountStatus.js";
import "../styles/SideBar.css";

const navItems = [
  {
    key: "compatibilidades",
    label: "COMPATIBILIDADES",
    icon: Boxes,
    hidden: true,
    children: [
      {
        to: "/compatibilidades/carga-masiva",
        label: "Carga Masiva de Compatibilidades",
        icon: FileSpreadsheet,
      },
      {
        to: "/compatibilidades/no-compatibilidades",
        label: "Informar No Compatibilidades",
        icon: Ban,
      },
    ],
  },
  {
    key: "actualizaciones",
    label: "ACTUALIZACIONES",
    icon: BadgeDollarSign,
    hidden: true,
    children: [
      {
        to: "/actualizaciones/precios-stock",
        label: "Actualizaciones de Precios y Stock",
        icon: FileSpreadsheet,
      },
    ],
  },

  {
    key: "procesos",
    label: "PROCESOS",
    icon: FolderSync,
    children: [
      {
        to: "/procesos/sincronizacion-procesos",
        label: "Sincronización de Procesos",
        icon: FileSpreadsheet,
      },
      {
        to: "/procesos/carga-familias-compatibilidades",
        label: "Carga de Familias-Compatibilidades",
        icon: FolderTree,
      },
      {
        to: "/procesos/copia-compatibilidades",
        label: "Copia de Compatibilidades",
        icon: CopyPlus,
      },
      {
        to: "/procesos/formatos-excel",
        label: "Formatos Excel",
        icon: FileSpreadsheet,
      },
    ],
  },
  {
    key: "gestion",
    label: "GESTION",
    children: [

      {
        to: "/gestion/ventas",
        label: "Gestión de Pedidos",
        icon: ShoppingCart,
        hidden: true,
      },

      {
        to: "/gestion/devoluciones",
        label: "Devoluciones",
        icon: Undo2,
      },
      {
        to: "/gestion/descargar-publicaciones",
        label: "Descargar Publicaciones",
        icon: Download,
      },

    ],
  },
  {
    key: "integraciones",
    label: "INTEGRACIONES",
    children: [
      {
        to: "/integraciones/proveedores",
        label: "Integración Proveedores",
        icon: Plug,
      },
    ],
  },
];

function SidebarAccountStatus() {
  const { userLabel, mlStatusLabel, mlStatusLoading, isMlConnected } =
    useAccountStatus({ refreshInterval: 300000 });

  return (
    <div className="sidebar__account">
      <span className="sidebar__account-caption">ESTADO DE CONEXIÓN</span>
      <span
        className={`sidebar__account-status ${
          mlStatusLoading
            ? "sidebar__account-status--pending"
            : isMlConnected
              ? "sidebar__account-status--success"
              : "sidebar__account-status--danger"
        }`}
      >
        {mlStatusLabel}
      </span>
      <span className="sidebar__account-email" title={userLabel}>{userLabel}</span>
    </div>
  );
}

export default function Sidebar({ sidebarOpen, setSidebarOpen, projectionMode = false }) {
  const location = useLocation();
  const isChildActive = (to) => location.pathname === to;
  const processItems = navItems.find((group) => group.key === "procesos")?.children || [];
  const gestionItems = navItems.find((group) => group.key === "gestion")?.children || [];
  const integrationItems =
    navItems.find((group) => group.key === "integraciones")?.children || [];

  return (
    <>
      <div
        className={`sidebar-overlay ${sidebarOpen ? "sidebar-overlay--show" : ""}`}
        onClick={() => setSidebarOpen(false)}
      />

      <aside
        className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}
        inert={projectionMode && !sidebarOpen ? true : undefined}
      >
        <div className="sidebar__header">
          <div className="sidebar__branding">
            <div className="sidebar__brand-stack">
              <img src={logo} alt="Repnet" className="sidebar__brand-logo" />
            </div>
          </div>

          <button
            className="sidebar__close"
            onClick={() => setSidebarOpen(false)}
            aria-label={projectionMode ? "Ocultar menú lateral" : "Cerrar menú"}
            title={projectionMode ? "Ocultar menú lateral" : "Cerrar menú"}
            type="button"
          >
            <X size={19} />
          </button>
        </div>

        <nav className="sidebar__nav">
          <span className="sidebar__section-label">PROCESOS</span>
          {processItems.map((item) => {
            const ItemIcon = item.icon;
            const active = isChildActive(item.to);

            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setSidebarOpen(false)}
                className={`sidebar__sublink ${
                  active ? "sidebar__sublink--active" : ""
                }`}
              >
                <span className="sidebar__sublink-icon">
                  <ItemIcon size={17} strokeWidth={2} />
                </span>
                <span className="sidebar__sublink-text">{item.label}</span>
              </NavLink>
            );
          })}

          <span className="sidebar__section-label sidebar__section-label--spaced">
            GESTION
          </span>

          {gestionItems.filter((item) => !item.hidden).map((item) => {
            const ItemIcon = item.icon;
            const active = isChildActive(item.to);

            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setSidebarOpen(false)}
                className={`sidebar__sublink ${
                  active ? "sidebar__sublink--active" : ""
                }`}
              >
                <span className="sidebar__sublink-icon">
                  <ItemIcon size={17} strokeWidth={2} />
                </span>
                <span className="sidebar__sublink-text">{item.label}</span>
              </NavLink>
            );
          })}

          <span className="sidebar__section-label sidebar__section-label--spaced">
            INTEGRACIONES
          </span>

          {integrationItems.map((item) => {
            const ItemIcon = item.icon;
            const active = isChildActive(item.to);

            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setSidebarOpen(false)}
                className={`sidebar__sublink ${
                  active ? "sidebar__sublink--active" : ""
                }`}
              >
                <span className="sidebar__sublink-icon">
                  <ItemIcon size={17} strokeWidth={2} />
                </span>
                <span className="sidebar__sublink-text">{item.label}</span>
              </NavLink>
            );
          })}

        </nav>
        {projectionMode && <SidebarAccountStatus />}
      </aside>
    </>
  );
}
