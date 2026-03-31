import React from "react";
import "../styles/Login.css";
import loginVisual from "../../../src/assets/repnetmercadolibre_logo.png"; // ajusta esta ruta si cambia

const MailIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" className="input-icon" aria-hidden="true">
    <path
      d="M4 7.5L10.94 12.46C11.57 12.91 12.43 12.91 13.06 12.46L20 7.5"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <rect
      x="3"
      y="5"
      width="18"
      height="14"
      rx="2.5"
      stroke="currentColor"
      strokeWidth="1.8"
    />
  </svg>
);

const LockIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" className="input-icon" aria-hidden="true">
    <path
      d="M8 10V7.75C8 5.68 9.68 4 11.75 4H12.25C14.32 4 16 5.68 16 7.75V10"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    />
    <rect
      x="5"
      y="10"
      width="14"
      height="10"
      rx="2.5"
      stroke="currentColor"
      strokeWidth="1.8"
    />
  </svg>
);

export default function Login() {
  const handleSubmit = (e) => {
    e.preventDefault();
    // Aquí conectas tu lógica de login
  };

  return (
    <main className="login-page">
      <div className="login-layout">
        <section className="login-left">
          <div className="brand-visual-wrapper">
            <img
              src={loginVisual}
              alt="Marcas asociadas"
              className="brand-visual"
            />
          </div>
        </section>

        <section className="login-right">
          <div className="login-card">
            <div className="login-card-content">

              <form className="login-form" onSubmit={handleSubmit}>
                <div className="input-group">
                  <span className="icon-wrapper">
                    <MailIcon />
                  </span>
                  <input
                    type="email"
                    name="email"
                    placeholder="Usuario"
                    autoComplete="email"
                    required
                  />
                </div>

                <div className="input-group">
                  <span className="icon-wrapper">
                    <LockIcon />
                  </span>
                  <input
                    type="password"
                    name="password"
                    placeholder="Password"
                    autoComplete="current-password"
                    required
                  />
                </div>

                <button type="submit" className="login-btn">
                  Ingresar
                </button>

                <button type="button" className="forgot-link">
                  ¿Olvidaste tu contraseña?
                </button>
              </form>
            </div>
          </div>

          <div className="curve curve-one"></div>
          <div className="curve curve-two"></div>
        </section>
      </div>
    </main>
  );
}