// Манифест расширения — чистая функция (её проверяет tests/manifest.test.ts). Разрешения
// минимальные и каждое обосновано в docs/EXTENSION_STORE.md:
// - storage — токен устройства, журнал write-операций, состояние для popup;
// - alarms — переподключение WebSocket и опрос привязки после остановки service worker;
// - scripting — выполнение операции в странице площадки (executeScript, world MAIN);
// - host: только наш API; сайты площадок — optional, по явному согласию пользователя.
// Нет cookies, tabs, webRequest и content scripts.

import { optionalOrigins } from "./platforms";

export interface ManifestOptions {
  apiBase: string;
  browser: string;
  geckoId: string;
}

export const PERMISSIONS = ["storage", "alarms", "scripting"] as const;

export function apiOrigin(apiBase: string): string {
  const url = new URL(apiBase);
  return `${url.protocol}//${url.host}/*`;
}

export function buildManifest(options: ManifestOptions): Record<string, unknown> {
  const manifest: Record<string, unknown> = {
    name: "SyncPlaylists",
    // Chrome Web Store: не длиннее 132 символов.
    description:
      "Переносит плейлисты и лайки между музыкальными площадками в вашем браузере — " +
      "по вашей команде на сайте SyncPlaylists.",
    permissions: [...PERMISSIONS],
    host_permissions: [apiOrigin(options.apiBase)],
    optional_host_permissions: optionalOrigins(),
  };
  if (options.browser === "firefox") {
    manifest.browser_specific_settings = {
      gecko: {
        id: options.geckoId,
        // executeScript({world: "MAIN"}) — с Firefox 128.
        strict_min_version: "128.0",
        // Встроенное согласие Firefox на передачу данных (AMO): метаданные плейлистов и
        // треков с сайтов площадок уходят на сервер SyncPlaylists.
        data_collection_permissions: { required: ["websiteContent"] },
      },
    };
  } else {
    // WebSocket-трафик продлевает жизнь service worker — с Chrome 116.
    manifest.minimum_chrome_version = "116";
  }
  return manifest;
}
