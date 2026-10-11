import { readStored, writeStored } from "./storage";

export type ThemePreference = "dark" | "light" | "system";

const KEY = "sp-theme";

export function getThemePreference(): ThemePreference {
  const stored = readStored(KEY);
  return stored === "light" || stored === "system" ? stored : "dark";
}

/** Тёмная тема по умолчанию; «system» следует настройке устройства. */
export function resolveTheme(preference: ThemePreference): "dark" | "light" {
  if (preference !== "system") return preference;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(preference: ThemePreference): void {
  const theme = resolveTheme(preference);
  document.documentElement.classList.toggle("dark", theme === "dark");
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#000000" : "#f2f2f7");
}

export function setThemePreference(preference: ThemePreference): void {
  writeStored(KEY, preference);
  applyTheme(preference);
}
