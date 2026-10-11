import type { Schemas } from "@/shared/api";

export type Platform = Schemas["Platform"];

export type PlatformInfo = {
  id: Platform;
  name: string;
  /** Сайт, где пользователь входит в браузере (для подсказок «войдите на …»). */
  site: string;
  /** Цвет плашки-значка: узнаваемый оттенок площадки, без её логотипа. */
  color: string;
  letter: string;
  /** Работает ли площадка в сервисе сейчас (через расширение). */
  available: boolean;
};

export const PLATFORMS: readonly PlatformInfo[] = [
  {
    id: "yandex",
    name: "Яндекс Музыка",
    site: "music.yandex.ru",
    color: "#ffcc00",
    letter: "Я",
    available: true,
  },
  { id: "vk", name: "VK Музыка", site: "vk.com", color: "#0077ff", letter: "VK", available: true },
  {
    id: "soundcloud",
    name: "SoundCloud",
    site: "soundcloud.com",
    color: "#ff5500",
    letter: "SC",
    available: true,
  },
  {
    id: "spotify",
    name: "Spotify",
    site: "open.spotify.com",
    color: "#1db954",
    letter: "S",
    available: false,
  },
  {
    id: "ytmusic",
    name: "YouTube Music",
    site: "music.youtube.com",
    color: "#ff0033",
    letter: "YT",
    available: false,
  },
];

export function platformInfo(id: Platform): PlatformInfo {
  const info = PLATFORMS.find((p) => p.id === id);
  if (!info) throw new Error(`Неизвестная площадка ${id}`);
  return info;
}
