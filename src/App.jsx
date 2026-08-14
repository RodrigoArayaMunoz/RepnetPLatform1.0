import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import MainLayout from "./frontend/layouts/MainLayout";
import Home from "./frontend/pages/Home";
import UploadFamily from "./frontend/pages/CompatibilitiesUpload";
import PreciosStock from "./frontend/pages/PriceStocksUploads";
import NoCompatibilidades from "./frontend/pages/NoCompatibilities";
import Login from "./frontend/pages/Login";
import { isSupabaseConfigured, supabase } from "./lib/supabase";
import MainSyncJobs from "./frontend/pages/MainSyncJobs.jsx";
import CompatibilityCopy from "./frontend/pages/CompatibilityCopy.jsx";
import DownloadPublications from "./frontend/pages/DownloadPublications.jsx";
import Returns from "./frontend/pages/Returns.jsx";
import SellerSales from "./frontend/pages/SellerSales.jsx";

function AuthLoadingScreen() {
  return <div className="app-root" />;
}

function RequireAuth({ canAccessProtectedRoutes, authLoading, children }) {
  if (authLoading) {
    return <AuthLoadingScreen />;
  }

  return canAccessProtectedRoutes ? children : <Navigate to="/" replace />;
}

export default function App() {
  const [session, setSession] = useState(null);
  const [hasAuthenticatedInApp, setHasAuthenticatedInApp] = useState(false);
  const [authLoading, setAuthLoading] = useState(isSupabaseConfigured);

  useEffect(() => {
    if (!supabase) {
      return undefined;
    }

    let isMounted = true;

    supabase.auth.getSession().then(({ data, error }) => {
      if (error) {
        console.error("No se pudo obtener la sesion actual.", error);
      }

      if (!isMounted) {
        return;
      }

      const restoredSession = data?.session ?? null;
      setSession(restoredSession);
      setHasAuthenticatedInApp(Boolean(restoredSession));
      setAuthLoading(false);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!isMounted) {
        return;
      }

      setSession(nextSession);

      if (event === "INITIAL_SESSION") {
        setHasAuthenticatedInApp(Boolean(nextSession));
      }

      if (event === "SIGNED_IN") {
        setHasAuthenticatedInApp(true);
      }

      if (event === "SIGNED_OUT") {
        setHasAuthenticatedInApp(false);
      }

      setAuthLoading(false);
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, []);

  const canAccessProtectedRoutes = Boolean(session && hasAuthenticatedInApp);

  const handleLoginSuccess = (nextSession) => {
    setSession(nextSession);
    setHasAuthenticatedInApp(true);
  };

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            <Login
              supabaseConfigured={isSupabaseConfigured}
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              onLoginSuccess={handleLoginSuccess}
            />
          }
        />

        <Route
          path="/menu"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Home />} />
        </Route>

        <Route
          path="/compatibilidades/carga-masiva"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<UploadFamily />} />
        </Route>

        <Route
          path="/compatibilidades/no-compatibilidades"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<NoCompatibilidades />} />
        </Route>

        <Route
          path="/actualizaciones/precios-stock"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<PreciosStock />} />
        </Route>

        <Route
          path="/procesos/carga-familias-compatibilidades"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<UploadFamily />} />
        </Route>

        <Route
          path="/procesos/sincronizacion-procesos"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<MainSyncJobs />} />
        </Route>

        <Route
          path="/procesos/copia-compatibilidades"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<CompatibilityCopy />} />
        </Route>

        <Route
          path="/gestion/descargar-publicaciones"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route
            index
            element={
              <DownloadPublications
                authUserId={session?.user?.id || ""}
              />
            }
          />
        </Route>

        <Route
          path="/gestion/devoluciones"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Returns />} />
        </Route>

        <Route
          path="/gestion/ventas"
          element={
            <RequireAuth
              canAccessProtectedRoutes={canAccessProtectedRoutes}
              authLoading={authLoading}
            >
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<SellerSales />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
