import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import Sidebar from "../components/SideBar";
import TopMenuBar from "../components/TopMenuBar";
import "../styles/MainLayout.css";

function RouteLoadingOverlay({ visible }) {
  if (!visible) return null;

  return (
    <div className="route-loading-overlay">
      <div className="route-loading-box">
        <div className="route-loading-spinner" />
        <p className="route-loading-text">Cargando módulo...</p>
      </div>
    </div>
  );
}

export default function MainLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();
  const isReturnsProjection = location.pathname === "/gestion/devoluciones";
  const [displayedPath, setDisplayedPath] = useState(location.pathname);
  const routeLoading = displayedPath !== location.pathname;

  useEffect(() => {
    if (!routeLoading) return undefined;
    const timer = setTimeout(() => setDisplayedPath(location.pathname), 600);
    return () => clearTimeout(timer);
  }, [location.pathname, routeLoading]);

  return (
    <div className={`layout ${isReturnsProjection ? "layout--projection" : ""}`}>
      <Sidebar
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        projectionMode={isReturnsProjection}
      />

      <div className="layout__main">
        {isReturnsProjection ? (
          !sidebarOpen && (
            <button
              type="button"
              className="layout__projection-menu-button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Mostrar menú lateral"
              title="Mostrar menú lateral"
            >
              <Menu size={24} aria-hidden="true" />
            </button>
          )
        ) : (
          <TopMenuBar onOpenSidebar={() => setSidebarOpen(true)} />
        )}

        <main className="layout__content">
          <div className={`layout__page ${routeLoading ? "layout__page--hidden" : ""}`}>
            <Outlet />
          </div>

          <RouteLoadingOverlay visible={routeLoading} />
        </main>
      </div>
    </div>
  );
}
