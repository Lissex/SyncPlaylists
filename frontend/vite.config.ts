import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Один origin с API: /api проксируется на бэкенд без префикса (как nginx в Docker).
// Тогда cookie сессии (SameSite=Lax) и SSE работают без CORS.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    // Файловые маршруты — в слое app (FSD): страницы лежат в pages, маршрут их только подключает.
    tanstackRouter({
      target: "react",
      routesDirectory: "./src/app/routes",
      generatedRouteTree: "./src/app/routeTree.gen.ts",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    // Все интерфейсы: и IPv4/IPv6 localhost (на Windows Vite иначе слушает только ::1),
    // и телефон в той же сети — проверять мобильную вёрстку на живом устройстве.
    host: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
