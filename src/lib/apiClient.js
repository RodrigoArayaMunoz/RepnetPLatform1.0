import { supabase } from "./supabase.js";

export async function authFetch(input, init = {}) {
  if (!supabase) {
    return fetch(input, init);
  }

  const { data } = await supabase.auth.getSession();
  const accessToken = data?.session?.access_token;
  const headers = new Headers(init.headers || {});

  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  return fetch(input, {
    ...init,
    headers,
  });
}

export async function startMercadoLibreLogin(apiBase, redirectTo) {
  const params = new URLSearchParams();
  if (redirectTo) {
    params.set("redirect_to", redirectTo);
  }

  const response = await authFetch(
    `${apiBase}/auth/login-url${params.toString() ? `?${params.toString()}` : ""}`,
    {
      method: "GET",
      credentials: "include",
    }
  );
  const data = await response.json().catch(() => ({}));

  if (!response.ok || !data?.url) {
    throw new Error(
      data?.detail || data?.message || "No se pudo iniciar la conexion con Mercado Libre."
    );
  }

  window.location.href = data.url;
}
