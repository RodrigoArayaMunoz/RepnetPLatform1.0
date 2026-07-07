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
  ClipboardList,
  RotateCcw,
  ShieldCheck,
  Store,
} from "lucide-react";
import logo from "../../assets/repnetsolo_logo.png";
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
    ],
  },
  {
    key: "gestion",
    label: "GESTION",
    children: [
      {
        to: "/gestion/devoluciones",
        label: "Gestión Devoluciones",
        icon: RotateCcw,
      },
      {
        to: "/gestion/garantias",
        label: "Gestión Garantías",
        icon: ShieldCheck,
      },
    ],
  },
  {
    key: "vendedor",
    label: "VENDEDOR",
    icon: Store,
    children: [
      {
        to: "/vendedor/solicitud-pedido",
        label: "Solicitud de Pedido",
        icon: ClipboardList,
      },
    ],
  },
];

export default function Sidebar({ sidebarOpen, setSidebarOpen }) {
  const location = useLocation();
  const isChildActive = (to) => location.pathname === to;
  const processItems = navItems.find((group) => group.key === "procesos")?.children || [];
  const gestionItems = navItems.find((group) => group.key === "gestion")?.children || [];
  const adminItems = navItems.find((group) => group.key === "vendedor")?.children || [];

  return (
    <>
      <div
        className={`sidebar-overlay ${sidebarOpen ? "sidebar-overlay--show" : ""}`}
        onClick={() => setSidebarOpen(false)}
      />

      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="sidebar__header">
          <div className="sidebar__branding">
            <div className="sidebar__brand-stack">
              <img src={logo} alt="Repnet" className="sidebar__brand-logo" />
            </div>
          </div>

          <button
            className="sidebar__close"
            onClick={() => setSidebarOpen(false)}
            aria-label="Cerrar menu"
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

          {gestionItems.map((item) => {
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
            ADMINISTRACION
          </span>

          {adminItems.map((item) => {
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
      </aside>
    </>
  );
}
