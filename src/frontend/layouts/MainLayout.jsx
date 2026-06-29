import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
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
  const [routeLoading, setRouteLoading] = useState(false);
  const location = useLocation();
  const previousPathRef = useRef(location.pathname);

  useEffect(() => {
    if (previousPathRef.current !== location.pathname) {
      setRouteLoading(true);

      const timer = setTimeout(() => {
        setRouteLoading(false);
        previousPathRef.current = location.pathname;
      }, 600);

      return () => clearTimeout(timer);
    }
  }, [location.pathname]);

  return (
    <div className="layout">
      <Sidebar
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
      />

      <div className="layout__main">
        <TopMenuBar onOpenSidebar={() => setSidebarOpen(true)} />

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
