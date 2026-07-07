import { useEffect, useState } from "react";
import {
  ArrowRight,
  Eye,
  EyeOff,
  LockKeyhole,
  Mail,
  Package,
  RefreshCw,
  ShieldCheck,
  Truck,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import "../styles/Login.css";
import mercadoLibreLogo from "../../../src/assets/mercadolibre_logo.png";
import repnetLogo from "../../../src/assets/repnetsolo_logo.png";
import {
  supabase,
  supabaseConfigErrorMessage,
} from "../../lib/supabase.js";

const brandFeatures = [
  { label: "Logistica", icon: Truck },
  { label: "Sincronizacion", icon: RefreshCw },
  { label: "Inventario", icon: Package },
];

export default function Login({
  supabaseConfigured,
  canAccessProtectedRoutes,
  onLoginSuccess,
}) {
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const authErrorMsg = supabaseConfigured
    ? errorMsg
    : supabaseConfigErrorMessage;

  useEffect(() => {
    if (!supabaseConfigured) {
      return;
    }

    const params = new URLSearchParams(location.search);
    const meliConnected = params.get("meli");
    const legacyConnected = params.get("ml_connected");

    if (
      canAccessProtectedRoutes &&
      (meliConnected === "connected" || legacyConnected === "1")
    ) {
      navigate(
        {
          pathname: "/menu",
          search: params.toString() ? `?${params.toString()}` : "",
        },
        { replace: true }
      );
    }
  }, [canAccessProtectedRoutes, location.search, navigate, supabaseConfigured]);

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (loading) {
      return;
    }

    if (!supabaseConfigured || !supabase) {
      setErrorMsg(supabaseConfigErrorMessage);
      return;
    }

    setErrorMsg("");
    setLoading(true);

    const startTime = Date.now();
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    const elapsed = Date.now() - startTime;
    const remaining = Math.max(2500 - elapsed, 0);

    await wait(remaining);

    if (error) {
      setLoading(false);
      setErrorMsg("Correo o contrasena incorrectos");
      return;
    }

    onLoginSuccess?.(data.session);
    navigate("/menu", { replace: true });
  };

  return (
    <>
      {loading && (
        <div className="login-loading-overlay" aria-live="polite" aria-busy="true">
          <div className="login-loading-box">
            <div className="login-spinner-wrap">
              <div className="login-spinner"></div>
              <img src={repnetLogo} alt="Repnet" className="login-spinner-logo" />
            </div>
          </div>
        </div>
      )}

      <main className={`login-page ${loading ? "login-page--blocked" : ""}`}>
        <div className="login-layout">
          <section className="login-brand-panel">
            <header className="login-brand-header">
              
            </header>

            <div className="login-brand-copy">
              <div className="login-sync-pill">
                <span aria-hidden="true"></span>
                Sincronizado con Mercado Libre
              </div>

              <h1>
                Gestion de repuestos,
                <span>simple y en tiempo real.</span>
              </h1>
              <p>
                Ingresa a tu panel para sincronizar catalogo, cargar familias y
                copiar compatibilidades entre publicaciones.
              </p>

              <div
                className="login-partner-row"
                aria-label="Integracion Repnet y Mercado Libre"
              >
                <div className="login-partner-chip">
                  <img src={repnetLogo} alt="Repnet.cl" />
                </div>
                <div className="login-partner-line" aria-hidden="true"></div>
                <div className="login-partner-chip login-partner-chip--meli">
                  <img src={mercadoLibreLogo} alt="Mercado Libre" />
                </div>
              </div>
            </div>

            <div className="brand-feature-row" aria-label="Funciones principales">
              {brandFeatures.map((feature) => (
                <div className="brand-feature" key={feature.label}>
                  <feature.icon
                    className="brand-feature-icon"
                    aria-hidden="true"
                  />
                  <span>{feature.label}</span>
                </div>
              ))}
            </div>

          </section>

          <section className="login-form-panel">
            <div className="login-panel">
              <div className="login-panel-heading">
                <h2>Ingresa a tu panel</h2>
                <p>Usa tu correo corporativo para acceder al ERP.</p>
              </div>

              <form className="login-form" onSubmit={handleSubmit}>
                <div className="form-field">
                  <label className="input-label" htmlFor="email">
                    Correo electronico
                  </label>
                  <div className="input-group">
                    <Mail className="input-icon" aria-hidden="true" />
                    <input
                      id="email"
                      type="email"
                      name="email"
                      placeholder="nombre@empresa.cl"
                      autoComplete="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      disabled={loading || !supabaseConfigured}
                    />
                  </div>
                </div>

                <div className="form-field">
                  <label className="input-label" htmlFor="password">
                    Contraseña
                  </label>
                  <div className="input-group">
                    <LockKeyhole className="input-icon" aria-hidden="true" />
                    <input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      name="password"
                      placeholder="Ingresa tu contrasena"
                      autoComplete="current-password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      disabled={loading || !supabaseConfigured}
                    />
                    <button
                      type="button"
                      className="password-toggle"
                      onClick={() => setShowPassword((current) => !current)}
                      disabled={loading}
                      aria-label={
                        showPassword ? "Ocultar contrasena" : "Mostrar contrasena"
                      }
                    >
                      {showPassword ? (
                        <EyeOff aria-hidden="true" />
                      ) : (
                        <Eye aria-hidden="true" />
                      )}
                    </button>
                  </div>
                </div>

                {authErrorMsg && (
                  <p className="auth-message error">{authErrorMsg}</p>
                )}

                <button
                  type="submit"
                  className="login-btn"
                  disabled={loading || !supabaseConfigured}
                >
                  <span>Ingresar</span>
                  <ArrowRight aria-hidden="true" />
                </button>
              </form>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
