import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { Bell, Menu } from "lucide-react";
import { supabase } from "../../lib/supabase.js";
import { readMlConnectionStatus } from "../../lib/meliConnection.js";

const ROUTE_LABELS = {
  "/menu": ["Inicio"],
  "/procesos/sincronizacion-procesos": ["Procesos", "Sincronizacion"],
  "/procesos/carga-familias-compatibilidades": [
    "Procesos",
    "Carga de Familias",
  ],
  "/procesos/copia-compatibilidades": ["Procesos", "Copia de Compatibilidades"],
  "/compatibilidades/carga-masiva": ["Compatibilidades", "Carga Masiva"],
  "/compatibilidades/no-compatibilidades": [
    "Compatibilidades",
    "No Compatibilidades",
  ],
  "/actualizaciones/precios-stock": ["Actualizaciones", "Precios y Stock"],
  "/vendedor/solicitud-pedido": ["Administracion", "Solicitud de Pedido"],
};

export default function TopMenuBar({ onOpenSidebar }) {
  const location = useLocation();
  const [userEmail, setUserEmail] = useState("");
  const [authLoading, setAuthLoading] = useState(true);
  const [isMlConnected, setIsMlConnected] = useState(false);
  const [mlStatusLoading, setMlStatusLoading] = useState(true);

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
      setIsMlConnected(false);
      return;
    }

    if (authLoading || !userEmail) {
      if (!authLoading && !userEmail) {
        setIsMlConnected(false);
        setMlStatusLoading(false);
      }
      return;
    }

    let cancelled = false;

    const checkMlConnection = async () => {
      setMlStatusLoading(true);

      try {
        const connection = await readMlConnectionStatus();

        if (!cancelled) {
          setIsMlConnected(connection.connected);
        }
      } catch (error) {
        console.error("Error inesperado verificando conexion ML:", error);

        if (!cancelled) {
          setIsMlConnected(false);
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

  const userLabel = useMemo(() => {
    if (authLoading) {
      return "Cargando...";
    }

    return userEmail || "No se encontro usuario autenticado";
  }, [authLoading, userEmail]);

  const mlStatusLabel = mlStatusLoading
    ? "ML VERIFICANDO"
    : isMlConnected
    ? "MERCADOLIBRE CONECTADO"
    : "MERCADOLIBRE NO CONECTADO";
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
