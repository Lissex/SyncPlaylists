// Что расширение знает о площадках в этом браузере: вошли ли на сайте и под каким
// аккаунтом, нужна ли капча (storage.local — переживает перезапуск браузера; при задаче
// роутер всё равно сверяет аккаунт заново). Плюс итог подключения площадки к
// SyncPlaylists (connect_platform) — для popup.

import { browser } from "wxt/browser";

import type { PlatformIdWire, SessionState } from "../protocol";

export interface PlatformStatus {
  session: SessionState;
  account: string | null; // id аккаунта площадки (external_user_id)
  username: string | null;
  at: number;
}

// Подключение площадки к SyncPlaylists: какой аккаунт отправили и что ответил сервер.
export interface ConnectRecord {
  status: "pending" | "connected" | "conflict";
  account: string;
}

export type PlatformStatuses = Partial<Record<PlatformIdWire, PlatformStatus>>;
export type ConnectStatuses = Partial<Record<PlatformIdWire, ConnectRecord>>;

const STATES = "platformStates";
const CONNECTS = "platformConnects";
const PAUSED = "pausedPlatforms";

export async function readStatuses(): Promise<PlatformStatuses> {
  const stored = await browser.storage.local.get(STATES);
  return (stored[STATES] as PlatformStatuses | undefined) ?? {};
}

export async function writeStatus(platform: PlatformIdWire, status: PlatformStatus): Promise<void> {
  const all = await readStatuses();
  all[platform] = status;
  await browser.storage.local.set({ [STATES]: all });
}

export async function readConnects(): Promise<ConnectStatuses> {
  const stored = await browser.storage.local.get(CONNECTS);
  return (stored[CONNECTS] as ConnectStatuses | undefined) ?? {};
}

export async function writeConnect(platform: PlatformIdWire, record: ConnectRecord): Promise<void> {
  const all = await readConnects();
  all[platform] = record;
  await browser.storage.local.set({ [CONNECTS]: all });
}

// Площадки с переносами, ждущими расширение (из pong сервера). storage.session: service
// worker могут остановить между ping.
export async function readPaused(): Promise<PlatformIdWire[]> {
  const stored = await browser.storage.session.get(PAUSED);
  return (stored[PAUSED] as PlatformIdWire[] | undefined) ?? [];
}

export async function writePaused(platforms: PlatformIdWire[]): Promise<void> {
  await browser.storage.session.set({ [PAUSED]: platforms });
}

// Площадке нужно действие человека, которое расширение само не сделает.
export function needsUser(status: PlatformStatus | undefined): boolean {
  return status !== undefined && status.session !== "ok" && status.session !== "no_permission";
}
