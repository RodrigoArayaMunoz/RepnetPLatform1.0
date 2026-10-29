import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  envDir: "src/backend/compatibilties",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: [
        "logo.png",
        "pwa-192x192.png",
        "pwa-512x512.png",
        "pwa-maskable-512x512.png",
      ],
      manifest: {
        id: "/",
        name: "REPNET - Gestión de Procesos",
        short_name: "REPNET",
        description:
          "Gestión de publicaciones, compatibilidades y procesos REPNET.",
        lang: "es-CL",
        start_url: "/procesos/sincronizacion-procesos",
        scope: "/",
        display: "standalone",
        orientation: "portrait-primary",
        background_color: "#f6f8fc",
        theme_color: "#3483fa",
        icons: [
          {
            src: "/pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "/pwa-maskable-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
        shortcuts: [
          {
            name: "Sincronización de procesos",
            short_name: "Procesos",
            url: "/procesos/sincronizacion-procesos",
            icons: [
              {
                src: "/pwa-192x192.png",
                sizes: "192x192",
                type: "image/png",
              },
            ],
          },
        ],
      },
      workbox: {
        cleanupOutdatedCaches: true,
        navigateFallback: "/index.html",
        globPatterns: ["**/*.{js,css,html,png,svg,ico,webp}"],
      },
    }),
  ],
});
