import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  envDir: "src/backend/compatibilties",
  plugins: [react()],
  optimizeDeps: {
    include: ["exceljs/dist/exceljs.min.js"],
  },
});
