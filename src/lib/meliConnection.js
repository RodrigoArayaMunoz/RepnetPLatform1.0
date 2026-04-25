export const ML_VERIFYING_MESSAGE =
  "Verificando conexion con Mercado Libre...";
export const ML_CONNECTED_MESSAGE = "Conectado exitosamente";
export const ML_DISCONNECTED_MESSAGE =
  "Debes conectar tu cuenta de Mercado Libre";
export const ML_UNAVAILABLE_MESSAGE =
  "No se pudo verificar la conexion con Mercado Libre";
const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function readMlConnectionStatus() {
  try {
    const response = await fetch(`${API_BASE}/ml/status`, {
      method: "GET",
      credentials: "include",
    });

    const data = await response.json().catch(() => ({}));
    const connected = Boolean(response.ok && data?.connected === true);

    return {
      connected,
      verified: connected,
      userId: connected && data?.user_id ? String(data.user_id) : null,
      statusMessage: connected
        ? ML_CONNECTED_MESSAGE
        : ML_DISCONNECTED_MESSAGE,
    };
  } catch (error) {
    console.error("No se pudo leer meli_global_connection:", error);

    return {
      connected: false,
      verified: false,
      userId: null,
      statusMessage: ML_UNAVAILABLE_MESSAGE,
    };
  }
}
