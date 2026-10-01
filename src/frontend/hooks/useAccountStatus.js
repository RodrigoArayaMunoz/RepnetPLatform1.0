import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { supabase } from "../../lib/supabase.js";
import { readMlConnectionStatus } from "../../lib/meliConnection.js";

export default function useAccountStatus({ refreshInterval = 0 } = {}) {
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
      if (!mounted) return;
      if (error) {
        console.error("No se pudo obtener el usuario autenticado:", error);
        setUserEmail("");
      } else {
        setUserEmail(data?.user?.email || "");
      }
      setAuthLoading(false);
    };

    loadUser();
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
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
      return undefined;
    }

    if (authLoading || !userEmail) {
      if (!authLoading) {
        setIsMlConnected(false);
        setMlStatusLoading(false);
      }
      return undefined;
    }

    let cancelled = false;
    const checkMlConnection = async () => {
      setMlStatusLoading(true);
      try {
        const connection = await readMlConnectionStatus();
        if (!cancelled) setIsMlConnected(connection.connected);
      } catch (error) {
        console.error("Error inesperado verificando conexion ML:", error);
        if (!cancelled) setIsMlConnected(false);
      } finally {
        if (!cancelled) setMlStatusLoading(false);
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") checkMlConnection();
    };
    checkMlConnection();
    window.addEventListener("focus", checkMlConnection);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    const intervalId = refreshInterval > 0
      ? window.setInterval(checkMlConnection, refreshInterval)
      : null;

    return () => {
      cancelled = true;
      window.removeEventListener("focus", checkMlConnection);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (intervalId !== null) window.clearInterval(intervalId);
    };
  }, [authLoading, location.pathname, location.search, refreshInterval, userEmail]);

  const userLabel = useMemo(() => {
    if (authLoading) return "Cargando...";
    return userEmail || "No se encontró usuario autenticado";
  }, [authLoading, userEmail]);

  const mlStatusLabel = mlStatusLoading
    ? "ML VERIFICANDO"
    : isMlConnected
      ? "MERCADOLIBRE CONECTADO"
      : "MERCADOLIBRE NO CONECTADO";

  return { userLabel, mlStatusLabel, mlStatusLoading, isMlConnected };
}
