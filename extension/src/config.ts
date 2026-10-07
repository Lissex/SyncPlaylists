// Настройки сборки (WXT_* из .env). Других источников конфигурации у расширения нет.

export const API_BASE = (import.meta.env.WXT_API_BASE ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
export const WS_URL = `${API_BASE.replace(/^http/, "ws")}/extension/ws`;
export const PAIR_URL = import.meta.env.WXT_PAIR_URL ?? `${API_BASE}/extension/pair`;
// Тестовая операция diagnostics.echo — только в dev-сборках.
export const DIAGNOSTICS = import.meta.env.WXT_DIAGNOSTICS === "true";
