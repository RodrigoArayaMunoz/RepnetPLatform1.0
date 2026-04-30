import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  X,
  Boxes,
  ChevronDown,
  ChevronRight,
  BadgeDollarSign,
  User,
  PlugZap,
  LogOut,
  FileSpreadsheet,
  Ban,
  FolderSync,
} from "lucide-react";
import logo from "../../assets/repnetsolo_logo.png";
import { supabase } from "../../lib/supabase.js";
import {
  ML_VERIFYING_MESSAGE,
  readMlConnectionStatus,
} from "../../lib/meliConnection.js";
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
    ],
  },
];

export default function Sidebar({ sidebarOpen, setSidebarOpen }) {
  const location = useLocation();
  const navigate = useNavigate();
  const API_BASE =
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  const [userEmail, setUserEmail] = useState("");
  const [authLoading, setAuthLoading] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  const [isMlConnected, setIsMlConnected] = useState(false);
  const [mlStatusLoading, setMlStatusLoading] = useState(true);
  const [mlStatusMessage, setMlStatusMessage] = useState(ML_VERIFYING_MESSAGE);

  const getMenuStateFromPath = (pathname) => ({
    compatibilidades: pathname.startsWith("/compatibilidades"),
    actualizaciones: pathname.startsWith("/actualizaciones"),
    procesos: pathname.startsWith("/procesos"),
  });

  const [openMenus, setOpenMenus] = useState(
    getMenuStateFromPath(location.pathname)
  );

  useEffect(() => {
    setOpenMenus(getMenuStateFromPath(location.pathname));
  }, [location.pathname]);

  useEffect(() => {
    if (!supabase) {
      setAuthLoading(false);
      setMlStatusLoading(false);
      return undefined;
    }

    let mounted = true;

    const loadUser = async () => {
      setAuthLoading(true);

      const { data, error } = await supabase.auth.getUser();

      if (!mounted) {
        return;
      }

      if (error) {
        console.error("No se pudo obtener el usuario autenticado:", error);
        setUserEmail("");
        setAuthLoading(false);
        return;
      }

      setUserEmail(data?.user?.email || "");
      setAuthLoading(false);
    };

    loadUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) {
        return;
      }

      setUserEmail(session?.user?.email || "");
      setAuthLoading(false);
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (!supabase) {
      setMlStatusLoading(false);
      setMlStatusMessage("Sin configuracion");
      return;
    }

    if (authLoading || !userEmail) {
      if (!authLoading && !userEmail) {
        setIsMlConnected(false);
        setMlStatusLoading(false);
        setMlStatusMessage("No conectado");
      }
      return;
    }

    let cancelled = false;

    const checkMlConnection = async () => {
      setMlStatusLoading(true);
      setMlStatusMessage(ML_VERIFYING_MESSAGE);

      try {
        const connection = await readMlConnectionStatus();

        if (cancelled) {
          return;
        }

        setIsMlConnected(connection.connected);
        setMlStatusMessage(connection.statusMessage);
      } catch (error) {
        console.error("Error inesperado verificando conexion ML:", error);

        if (!cancelled) {
          setIsMlConnected(false);
          setMlStatusMessage("No se pudo verificar la conexion con Mercado Libre");
        }
      } finally {
        if (!cancelled) {
          setMlStatusLoading(false);
        }
      }
    };

    checkMlConnection();

    const handleWindowFocus = () => {
      checkMlConnection();
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        checkMlConnection();
      }
    };

    window.addEventListener("focus", handleWindowFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      cancelled = true;
      window.removeEventListener("focus", handleWindowFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [authLoading, location.pathname, location.search, userEmail]);

  const toggleMenu = (key) => {
    setOpenMenus((prev) => ({
      compatibilidades:
        key === "compatibilidades" ? !prev.compatibilidades : false,
      actualizaciones:
        key === "actualizaciones" ? !prev.actualizaciones : false,

      procesos:
        key === "procesos" ? !prev.procesos : false,
    }));
  };

  const isChildActive = (to) => location.pathname === to;

  const isGroupActive = (children) =>
    children.some((child) => location.pathname === child.to);

  const handleSignOut = async () => {
    if (signingOut) {
      return;
    }

    try {
      setSigningOut(true);

      const connection = await readMlConnectionStatus();

      if (connection.userId) {
        await fetch(
          `${API_BASE}/auth/logout?user_id=${encodeURIComponent(connection.userId)}`,
          {
            method: "POST",
            credentials: "include",
          }
        );
      }

      if (supabase) {
        const { error } = await supabase.auth.signOut();

        if (error) {
          throw error;
        }
      }

      setSidebarOpen(false);
      navigate("/", { replace: true });
    } catch (error) {
      console.error("Error al cerrar sesion:", error);
    } finally {
      setSigningOut(false);
    }
  };

  const mlStatusLabel = useMemo(() => {
    if (!supabase) {
      return "Sin configuracion";
    }

    if (mlStatusLoading) {
      return "Verificando...";
    }

    return isMlConnected ? "Conectado" : mlStatusMessage;
  }, [isMlConnected, mlStatusLoading, mlStatusMessage]);

  return (
    <>
      <div
        className={`sidebar-overlay ${sidebarOpen ? "sidebar-overlay--show" : ""}`}
        onClick={() => setSidebarOpen(false)}
      />

      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="sidebar__header">
          <div className="sidebar__branding">
            <img src={logo} alt="Repnet" className="sidebar__brand-logo" />
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

        <div className="sidebar__card sidebar__card--user">
          <div className="sidebar__user-row">
            <span className="sidebar__user-icon">
              <User size={16} />
            </span>

            <div className="sidebar__user-content">
              <span className="sidebar__user-title">Usuario:</span>
              <span className="sidebar__user-value">
                {authLoading
                  ? "Cargando..."
                  : userEmail || "No se encontro usuario autenticado"}
              </span>
            </div>
          </div>

          <div className="sidebar__user-row">
            <span className="sidebar__user-icon">
              <PlugZap size={16} />
            </span>

            <div className="sidebar__user-content">
              <span className="sidebar__user-title">
                Estado Mercado Libre:
              </span>
              <span
                className={`sidebar__status-badge ${
                  mlStatusLoading
                    ? "sidebar__status-badge--pending"
                    : isMlConnected
                    ? "sidebar__status-badge--success"
                    : "sidebar__status-badge--danger"
                }`}
              >
                {mlStatusLabel}
              </span>
            </div>
          </div>
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

        <div className="sidebar__footer">
          <button
            type="button"
            className="sidebar__logout-button"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            <LogOut size={18} />
            <span>{signingOut ? "Cerrando sesion..." : "Cerrar sesion"}</span>
          </button>
        </div>
      </aside>
    </>
  );
}
