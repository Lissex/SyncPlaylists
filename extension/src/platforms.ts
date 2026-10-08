// Площадки, с которыми работает расширение. Чистые данные (без browser API): их читает и
// сборка манифеста (optional_host_permissions), и код расширения.

export type PlatformId = "spotify" | "yandex" | "vk" | "soundcloud" | "ytmusic";

export interface PlatformInfo {
  id: PlatformId;
  title: string;
  // Доступ к сайту площадки (optional_host_permissions, выдаётся на экране согласия).
  // Только сам сайт: запросы к API площадки делает её же страница (fetch из MAIN world),
  // поэтому хосты api.* расширению не нужны.
  origins: string[];
  // Страница, которую расширение открывает в фоновой вкладке для операций.
  tabUrl: string;
  // false — площадка в списке, но включить её пока нельзя (операций ещё нет).
  available: boolean;
}

export const PLATFORMS: readonly PlatformInfo[] = [
  {
    id: "soundcloud",
    title: "SoundCloud",
    origins: ["https://soundcloud.com/*"],
    tabUrl: "https://soundcloud.com/",
    available: true,
  },
  {
    id: "yandex",
    title: "Яндекс Музыка",
    origins: ["https://music.yandex.ru/*"],
    tabUrl: "https://music.yandex.ru/",
    // Операции Яндекса — этап 4c-4; до них разрешение на сайт не просим.
    available: false,
  },
  {
    id: "spotify",
    title: "Spotify",
    origins: ["https://open.spotify.com/*"],
    tabUrl: "https://open.spotify.com/",
    available: false,
  },
  {
    id: "vk",
    title: "VK Музыка",
    origins: ["https://vk.com/*"],
    tabUrl: "https://vk.com/audio",
    available: false,
  },
];

export function platformInfo(id: string): PlatformInfo | undefined {
  return PLATFORMS.find((platform) => platform.id === id);
}

// Опциональные хосты манифеста — только у площадок, которые можно включить.
export function optionalOrigins(): string[] {
  return PLATFORMS.filter((platform) => platform.available).flatMap((p) => p.origins);
}

// «Площадка» тестовой операции diagnostics.echo — страница нашего же API. Она под
// обязательным host permission, отдельного согласия не требует.
export interface TargetInfo {
  key: string;
  origins: string[];
  tabUrl: string;
}

export function diagnosticsTarget(apiBase: string): TargetInfo {
  return {
    key: "diagnostics",
    origins: [`${apiBase}/*`],
    tabUrl: `${apiBase}/extension/diagnostics/page`,
  };
}

export function platformTarget(platform: PlatformInfo): TargetInfo {
  return { key: platform.id, origins: platform.origins, tabUrl: platform.tabUrl };
}
