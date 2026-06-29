import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  X,
  Boxes,
  ChevronDown,
  ChevronRight,
  BadgeDollarSign,
  FileSpreadsheet,
  Ban,
  FolderSync,
  FolderTree,
  ClipboardList,
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

  const getMenuStateFromPath = (pathname) => ({
    compatibilidades: pathname.startsWith("/compatibilidades"),
    actualizaciones: pathname.startsWith("/actualizaciones"),
    procesos: pathname.startsWith("/procesos"),
    vendedor: pathname.startsWith("/vendedor"),
  });

  const [openMenus, setOpenMenus] = useState(
    getMenuStateFromPath(location.pathname)
  );

  useEffect(() => {
    setOpenMenus(getMenuStateFromPath(location.pathname));
  }, [location.pathname]);

  const toggleMenu = (key) => {
    setOpenMenus((prev) => ({
      compatibilidades:
        key === "compatibilidades" ? !prev.compatibilidades : false,
      actualizaciones:
        key === "actualizaciones" ? !prev.actualizaciones : false,

      procesos:
        key === "procesos" ? !prev.procesos : false,
      vendedor:
        key === "vendedor" ? !prev.vendedor : false,
    }));
  };

  const isChildActive = (to) => location.pathname === to;

  const isGroupActive = (children) =>
    children.some((child) => location.pathname === child.to);

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
          {navItems
            .filter((group) => !group.hidden)
            .map((group) => {
            const GroupIcon = group.icon;
            const groupOpen = openMenus[group.key];
            const groupActive = isGroupActive(group.children);

            return (
              <div key={group.key} className="sidebar__group">
                <button
                  type="button"
                  className={`sidebar__group-button ${
                    groupActive ? "sidebar__group-button--active" : ""
                  }`}
                  onClick={() => toggleMenu(group.key)}
                  aria-expanded={groupOpen}
                >
                  <div className="sidebar__group-left">
                    <span className="sidebar__icon">
                      <GroupIcon size={20} />
                    </span>
                    <span className="sidebar__group-label">{group.label}</span>
                  </div>

                  <span className="sidebar__group-arrow">
                    {groupOpen ? (
                      <ChevronDown size={18} />
                    ) : (
                      <ChevronRight size={18} />
                    )}
                  </span>
                </button>

                <div
                  className={`sidebar__submenu ${
                    groupOpen ? "sidebar__submenu--open" : ""
                  }`}
                >
                  {group.children.map((item) => {
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
                          <ItemIcon size={18} />
                        </span>
                        <span className="sidebar__sublink-text">
                          {item.label}
                        </span>
                      </NavLink>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
