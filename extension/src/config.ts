// Настройки сборки (WXT_* из .env). Других источников конфигурации у расширения нет.

export const API_BASE = (import.meta.env.WXT_API_BASE ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
export const WS_URL = `${API_BASE.replace(/^http/, "ws")}/extension/ws`;
// Страница сайта, где вводят код привязки («Подключения»).
export const PAIR_URL = import.meta.env.WXT_PAIR_URL ?? "http://localhost:5173/connections";
