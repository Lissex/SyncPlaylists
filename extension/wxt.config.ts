import { defineConfig } from "wxt";

import { buildManifest } from "./src/manifest";

// Переменные WXT_* — из .env (см. .env.example); WXT загружает их до чтения конфига.
export default defineConfig({
  srcDir: "src",
  // Явные импорты (import { browser } from "wxt/browser") — без автоимпортов.
  imports: false,
  // Firefox по умолчанию собирается WXT как MV2 — нам нужен MV3 везде.
  manifestVersion: 3,
  manifest: ({ browser }) =>
    buildManifest({
      apiBase: (process.env.WXT_API_BASE ?? "http://localhost:8000").replace(/\/$/, ""),
      browser,
      geckoId: process.env.WXT_GECKO_ID ?? "syncplaylists@syncplaylists.local",
    }),
});
